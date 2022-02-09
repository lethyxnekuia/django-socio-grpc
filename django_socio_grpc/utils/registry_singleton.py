import inspect
from collections import OrderedDict
from typing import Dict, List, Tuple

from django.db import models
from rest_framework.fields import (
    DictField,
    HiddenField,
    ListField,
    ModelField,
    ReadOnlyField,
    SerializerMethodField,
)
from rest_framework.relations import ManyRelatedField, RelatedField, SlugRelatedField
from rest_framework.serializers import BaseSerializer, ListSerializer
from rest_framework.utils.model_meta import get_field_info

from django_socio_grpc.proto_serializers import BaseProtoSerializer, ListProtoSerializer
from django_socio_grpc.settings import grpc_settings
from django_socio_grpc.utils import model_meta
from django_socio_grpc.utils.tools import rreplace

DEFAULT_LIST_FIELD_NAME = "results"
REQUEST_SUFFIX = "Request"
RESPONSE_SUFFIX = "Response"


class RegisterServiceException(Exception):
    pass


class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(SingletonMeta, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class KnowMethods:
    LIST = "List"
    CREATE = "Create"
    RETRIEVE = "Retrieve"
    UPDATE = "Update"
    PARTIAL_UPDATE = "PartialUpdate"
    DESTROY = "Destroy"
    STREAM = "Stream"

    @classmethod
    def get_as_list(cls):
        return [
            cls.LIST,
            cls.CREATE,
            cls.RETRIEVE,
            cls.UPDATE,
            cls.PARTIAL_UPDATE,
            cls.DESTROY,
            cls.STREAM,
        ]

    @classmethod
    def get_methods_no_custom_messages(cls):
        return [cls.CREATE, cls.UPDATE, cls.PARTIAL_UPDATE]

    @classmethod
    def get_methods_no_stream(cls):
        return [
            cls.LIST,
            cls.CREATE,
            cls.RETRIEVE,
            cls.UPDATE,
            cls.PARTIAL_UPDATE,
            cls.DESTROY,
        ]


class RegistrySingleton(metaclass=SingletonMeta):
    """
    Registry Singleton is a singleton class allowing to grab all the service declared in grpc_settings.ROOT_HANDLERS_HOOK
    and introspect django model and serializer to determine the proto to generate
    """

    type_mapping = {
        # Numeric
        models.AutoField.__name__: "int32",
        models.SmallIntegerField.__name__: "int32",
        models.IntegerField.__name__: "int32",
        models.BigIntegerField.__name__: "int64",
        models.PositiveSmallIntegerField.__name__: "int32",
        models.PositiveIntegerField.__name__: "int32",
        models.FloatField.__name__: "float",
        models.DecimalField.__name__: "double",
        # Boolean
        models.BooleanField.__name__: "bool",
        models.NullBooleanField.__name__: "bool",
        # Date and time
        models.DateField.__name__: "string",
        models.TimeField.__name__: "string",
        models.DateTimeField.__name__: "string",
        models.DurationField.__name__: "string",
        # String
        models.CharField.__name__: "string",
        models.TextField.__name__: "string",
        models.EmailField.__name__: "string",
        models.SlugField.__name__: "string",
        models.URLField.__name__: "string",
        models.UUIDField.__name__: "string",
        models.GenericIPAddressField.__name__: "string",
        models.FilePathField.__name__: "string",
        # Other
        models.BinaryField.__name__: "bytes",
        # Default
        models.Field.__name__: "string",
    }

    # JSONField and PositiveBigIntegerField not available on Django 2.2
    try:
        # Special
        type_mapping[models.JSONField.__name__] = "google.protobuf.Struct"
    except AttributeError:
        from django.contrib.postgres.fields import JSONField

        type_mapping[JSONField.__name__] = "google.protobuf.Struct"

    try:
        type_mapping[models.PositiveBigIntegerField.__name__] = "int64"
    except AttributeError:
        pass

    _instances = {}

    @classmethod
    def clean_all(cls):
        cls._instances = {}

    def __init__(self):
        self.registered_app = OrderedDict()

    ############################################################################
    #
    # Common function used by both generation method (decorator and know_method)
    #
    ############################################################################

    def register_serializer_as_message_if_not_exist(
        self, app_name, serializer_instance, message_name=None, is_request=True
    ):
        """
        Register a message if not already exsting in the registered_messages of an app_name
        This message need to be in a correct format that will be used by generators to transform it into generators
        """
        if message_name is None:
            message_name = self.get_message_name_from_field_or_serializer_instance(
                serializer_instance, is_request
            )

        pk_name = None
        if getattr(serializer_instance.Meta, "model", None):
            pk_name = model_meta.get_model_pk(serializer_instance.Meta.model).name

        if message_name in self.registered_app[app_name]["registered_messages"]:
            return message_name

        self.registered_app[app_name]["registered_messages"][message_name] = []

        for field_name, field_type in serializer_instance.get_fields().items():

            # INFO - AM - 21/01/2022 - HiddenField are not used in api so not showed in protobuf file
            if issubclass(field_type.__class__, HiddenField):
                continue

            # INFO - AM - 21/01/2022 - if SEPARATE_READ_WRITE_MODEL is true (by default yes) then we need to filter read only or write only field depend of if is requets message or not
            # INFO - AM - 21/01/2022 - By defautl in DRF Pk are read_only. But in grpc we want them to be in the message
            if grpc_settings.SEPARATE_READ_WRITE_MODEL and field_name != pk_name:
                if is_request and self.field_type_is_read_only(field_type):
                    continue
                if not is_request and self.field_type_is_write_only(field_type):
                    continue

            field_grpc_generator_format = (
                field_name,
                self.get_proto_type(
                    app_name,
                    field_type,
                    field_name,
                    serializer_instance,
                    is_request=is_request,
                ),
            )

            self.registered_app[app_name]["registered_messages"][message_name].append(
                field_grpc_generator_format
            )

        return message_name

    def field_type_is_read_only(self, field_type):
        # INFO - AM - 07/01/2022 - If the field type inherit of ListProtoSerializer that mean we have
        if issubclass(field_type.__class__, ReadOnlyField):
            return True
        return field_type.read_only is True

    def field_type_is_write_only(self, field_type):
        return field_type.write_only is True

    def get_message_name_from_field_or_serializer_instance(
        self, class_or_field_instance, is_request=None, append_type=True
    ):
        # INFO - AM - 21/01/2022 - if SEPARATE_READ_WRITE_MODEL is true (by default yes) then we have two different message for the same serializer
        class_name = class_or_field_instance.__class__.__name__
        if "ProtoSerializer" in class_name:
            message_name = rreplace(class_name, "ProtoSerializer", "", 1)
        elif "Serializer" in class_name:
            message_name = rreplace(class_name, "Serializer", "", 1)
        else:
            message_name = class_name

        if grpc_settings.SEPARATE_READ_WRITE_MODEL and append_type:
            message_name = f"{message_name}{REQUEST_SUFFIX if is_request else RESPONSE_SUFFIX}"
        return message_name

    def get_proto_type(
        self, app_name, field_type, field_name, serializer_instance, is_request=None
    ):
        """
        Return a proto_type  to use in the proto file from a field type.
        For SerializerMethodField we also need field_name and serializer_instance
        """

        # If field type is a str that mean we use a custom field
        if isinstance(field_type, str):
            return field_type

        proto_type = self.type_mapping.get(field_type.__class__.__name__, "string")

        # if  field_name == "slug_many_many":
        #     print(f"class is : {field_type.__class__}")
        #     print(f"is subclas of ListProtoSerializer: {issubclass(field_type.__class__, ListProtoSerializer)}")
        #     print(f"is subclas of BaseProtoSerializer: {issubclass(field_type.__class__, BaseProtoSerializer)}")
        #     print(f"is subclas of SlugRelatedField: {issubclass(field_type.__class__, SlugRelatedField)}")
        #     print(
        #         f"is subclas of ManyRelatedField: {issubclass(field_type.__class__, ManyRelatedField)}"
        #     )
        #     print(f"is subclas of RelatedField: {issubclass(field_type.__class__, RelatedField)}")
        #     print(f"is subclas of ListSerializer: {issubclass(field_type.__class__, ListSerializer)}")
        #     print(f"is subclas of ListField: {issubclass(field_type.__class__, ListField)}")
        #     print(f"is subclas of DictField: {issubclass(field_type.__class__, DictField)}")
        #     print(f"is subclas of BaseSerializer: {issubclass(field_type.__class__, BaseSerializer)}")
        #     print(f"is subclas of SerializerMethodField: {issubclass(field_type.__class__, SerializerMethodField)}")

        # INFO - AM - 07/01/2022 - If the field type inherit of ListProtoSerializer that mean we have
        if issubclass(field_type.__class__, ListProtoSerializer):
            proto_type = f"repeated {self.get_message_name_from_field_or_serializer_instance(field_type.child, is_request=is_request)}"
            # INFO - AM - 07/01/2022 - If nested serializer not used anywhere else we need to add it too
            self.register_serializer_as_message_if_not_exist(
                app_name, field_type.child, is_request=is_request
            )

        # INFO - AM - 07/01/2022 - else if the field type inherit from proto serializer that mean that it is generated as a message in the proto file
        elif issubclass(field_type.__class__, BaseProtoSerializer):
            proto_type = self.get_message_name_from_field_or_serializer_instance(
                field_type, is_request=is_request
            )
            # INFO - AM - 07/01/2022 - If nested serializer not used anywhere else we need to add it too
            self.register_serializer_as_message_if_not_exist(
                app_name, field_type, is_request=is_request
            )

        # INFO - AM - 07/01/2022 - Else if the field type inherit from the SlugRelatedField that mean the type is the type name attribute in the foreign model
        elif issubclass(field_type.__class__, SlugRelatedField):
            proto_type = self.get_pk_from_slug_related_field(
                field_type, field_name, serializer_instance
            )

        # INFO - AM - 07/01/2022 - Else if the field type inherit from the ManyRelatedField that mean the type is the type of the pk of the child_relation (see relations.py of drf)
        elif issubclass(field_type.__class__, ManyRelatedField):
            child_proto_type = self.get_proto_type(
                app_name,
                field_type.child_relation,
                field_name,
                serializer_instance,
                is_request=is_request,
            )
            # INFO - AM - 03/02/2022 - if the returned child_proto_type returned is repeated (this can happen with slud related field in a many many relationships) we remove it because we only need one repeated
            if child_proto_type.startswith("repeated "):
                child_proto_type = child_proto_type[9:]
            proto_type = f"repeated {child_proto_type}"

        # INFO - AM - 07/01/2022 - Else if the field type inherit from the RelatedField that mean the type is the type of the pk of the foreign model
        elif issubclass(field_type.__class__, RelatedField):
            proto_type = self.get_pk_from_related_field(field_type)

        # INFO - AM - 07/01/2022 - Else if the field type inherit from the ListSerializer that mean it's a repaeated Struct
        elif issubclass(field_type.__class__, ListSerializer):
            proto_type = "repeated google.protobuf.Struct"

        # INFO - AM - 07/01/2022 - Else if the field type inherit from the ListField that mean it's a repaeated of the child attr proto type
        elif issubclass(field_type.__class__, ModelField):
            proto_type = self.type_mapping.get(
                field_type.model_field.__class__.__name__, "string"
            )

        # INFO - AM - 07/01/2022 - Else if the field type inherit from the ListField that mean it's a repaeated of the child attr proto type
        elif issubclass(field_type.__class__, ListField):
            child_type = self.type_mapping.get(field_type.child.__class__.__name__, "string")
            proto_type = f"repeated {child_type}"

        # INFO - AM - 07/01/2022 - Else if the field type inherit from the DictField that mean it's a Struct
        elif issubclass(field_type.__class__, DictField):
            proto_type = "google.protobuf.Struct"

        # INFO - AM - 07/01/2022 - Else if the field type inherit from the BaseSerializer that mean it's a Struct
        elif issubclass(field_type.__class__, BaseSerializer):
            proto_type = "google.protobuf.Struct"

        # INFO - AM - 07/01/2022 - Else if the field type inherit from the BaseSerializer that mean it's a Struct
        elif issubclass(field_type.__class__, SerializerMethodField):
            proto_type = self.get_proto_type_from_inspect(
                field_type, field_name, serializer_instance
            )

        return proto_type

    def get_proto_type_from_inspect(self, field_type, field_name, serializer_instance):
        """
        In some cases (for now only SerializerMethodField) we need to introspect method and ask user to specify the return type to be able to find the correct proto type
        """
        method_name = field_type.method_name
        if method_name is None:
            method_name = f"get_{field_name}"
        method = getattr(serializer_instance, method_name, None)
        if method is None:
            # TODO - AM - 21/01/2022 - What todo here ? raise an excpetion or let DRF handle this kind of problems ?
            return "string"

        if "return" not in method.__annotations__:
            raise RegisterServiceException(
                f"You are trying to register the serializer {serializer_instance.__class__.__name__} with a SerializerMethodField on the field {field_name}. But the method associated does'nt have a return annotations. Please look at the example: https://github.com/socotecio/django-socio-grpc/blob/master/django_socio_grpc/tests/fakeapp/serializers.py#L83. And the python doc: https://docs.python.org/3.8/library/typing.html"
            )

        python_type_to_proto_type = {
            int: "int32",
            str: "string",
            bool: "bool",
            float: "float",
            list: "repeated string",
            dict: "google.protobuf.Struct",
            bytes: "bytes",
            List: "repeated string",
            Dict: "google.protobuf.Struct",
            List[int]: "repeated int32",
            List[str]: "repeated string",
            List[bool]: "repeated bool",
            List[Tuple]: "repeated google.protobuf.Struct",
            List[Dict]: "repeated google.protobuf.Struct",
        }

        return python_type_to_proto_type[method.__annotations__["return"]]

    def get_pk_from_related_field(self, related_field):
        """
        When we have RelatedField (relation by id) we need to find the type of this relation.
        it can be specified by the pk_field or the queryset of the relatedfield
        """
        if related_field.pk_field:
            type_name = related_field.pk_field.__class__.__name__
        else:
            type_name = model_meta.get_model_pk(
                related_field.queryset.model
            ).__class__.__name__
        return self.type_mapping.get(type_name, "related_not_found")

    def get_pk_from_slug_related_field(
        self, slug_related_field, field_name, serializer_instance
    ):
        """
        When we have SlugRelatedField (relation by a field) we need to find the type of the field used in the relation by its name.
        it is specified by slug_field
        """

        if not hasattr(serializer_instance.Meta, "model"):
            print(
                f"GENERATION ERROR: No Model in serializer {serializer_instance.__class__.__name__} Meta but using a SlugRelatedField"
            )
            return "string"

        # INFO - AM - 27/01/2022 - get_field_info is drf utils methods to get all the informations about the fields and the relations of a model
        # See: https://github.com/encode/django-rest-framework/blob/master/rest_framework/utils/model_meta.py
        (
            pk,
            fields,
            forward_relations,
            reverse_relations,
            fields_and_pk,
            relationships,
        ) = get_field_info(serializer_instance.Meta.model)

        # INFO - AM - 27/01/2022 - the field name need to match with an existing relation ship to have a correct SlugRelatedField
        if field_name not in relationships:
            print(
                f"GENERATION ERROR: slug_related field name {field_name} not found in relationships of {serializer_instance.Meta.model}"
            )
            return "string"

        (
            model_field,
            related_model,
            to_many,
            to_field,
            has_through_model,
            reverse,
        ) = relationships[field_name]

        # INFO - AM - 27/01/2022 - A SlugRelatedFiel has a required slug_field attribute that is the name of the attibute in the related model we want to find the proto type
        slug_defered_attribute = getattr(related_model, slug_related_field.slug_field, None)
        if slug_defered_attribute is None:
            print(
                f"GENERATION ERROR: Related_Model_{str(related_model)}_as_no_field_{slug_related_field.slug_field}"
            )
            return "string"

        # INFO - AM - 27/01/2022 - As there is reverse relationship django return a slug_defered_attribute that has a field attribute that is the field that we want to find the prototype
        slug_field_class_name = slug_defered_attribute.field.__class__.__name__

        proto_type = self.type_mapping.get(slug_field_class_name, "slug_field type not found")

        # INFO - AM - 27/01/2022 - If to_many args is true that mean we have a repeated proto type
        if to_many:
            proto_type = f"repeated {proto_type}"

        return proto_type

    def register_list_message_of_serializer(
        self,
        app_name,
        service_instance,
        base_name,
        list_response_field_name,
        child_response_message_name,
        message_name=None,
        is_request=False,
    ):

        pagination = service_instance.pagination_class
        if pagination is None:
            pagination = grpc_settings.DEFAULT_PAGINATION_CLASS is not None

        response_fields = [
            (list_response_field_name, f"repeated {child_response_message_name}")
        ]
        if pagination:
            response_fields += [("count", "int32")]

        # INFO - AM - 04/02/2022 - For list message with a custom name we need to add List Before Response or Request end of world if seperate settings is true
        if message_name:
            if grpc_settings.SEPARATE_READ_WRITE_MODEL:
                suffix_len = 0
                if is_request and message_name.endswith(REQUEST_SUFFIX):
                    suffix_len = len(REQUEST_SUFFIX)
                elif not is_request and message_name.endswith(RESPONSE_SUFFIX):
                    suffix_len = len(RESPONSE_SUFFIX)

                response_message_name = (
                    message_name[:-suffix_len] + "List" + message_name[-suffix_len:]
                )
            else:
                response_message_name = f"{message_name}List"
        else:
            response_message_name = f"{base_name}List{RESPONSE_SUFFIX}"

        self.registered_app[app_name]["registered_messages"][
            response_message_name
        ] = response_fields

        return response_message_name

    def get_list_response_field_name_from_serializer_instance(self, serializer_instance):

        list_response_field_name = DEFAULT_LIST_FIELD_NAME

        # INFO - AM - 14/01/2022 - We let the possibility to the user to customize the name of the attr where the list items are set by message_list_attr attr in meta class. If not present we use the default results
        serializer_meta = getattr(serializer_instance, "Meta", None)
        if serializer_meta:
            message_list_attr = getattr(serializer_meta, "message_list_attr", None)
            if message_list_attr:
                list_response_field_name = message_list_attr

        return list_response_field_name

    ############################################################################
    #
    # Default Registration (from know method with no decorator)
    #
    ############################################################################
    def register_service(self, app_name, service_class):
        """ "
        For each service register in ROOT_HANDLERS_HOOK we try to register its controller and its messages
        """
        # print("-----------------\n"*5)
        # print("register_service", Service)

        service_instance = service_class()

        # INFO - AM - 07/01/2022 - Initialize the app in the project to be generated as a specific proto file
        if app_name not in self.registered_app:
            self.registered_app[app_name] = {
                "registered_controllers": OrderedDict(),
                "registered_messages": OrderedDict(),
            }

        self.set_controller_and_messages(app_name, service_instance)

    def set_controller_and_messages(self, app_name, service_instance):
        """
        Generate proto methods and messages for a service instance.
        First it try all know methods defined in the mixins used by ModelService.
        If not existing it do nothing
        If existing we look if it already register with a decorator that will prevent the default behavior
        If not already register that mean we want to use the default behavior so we just go with that and call register_default_message_from_method
        """
        service_name = service_instance.get_service_name()

        # INFO - AM - 07/01/2022 - Choose the name of the controler
        controller_name = f"{service_name}Controller"

        if controller_name not in self.registered_app[app_name]["registered_controllers"]:
            self.registered_app[app_name]["registered_controllers"][controller_name] = {}

        for method in KnowMethods.get_as_list():
            if not getattr(service_instance, method, None):
                continue

            # If we already have registered this method for this controlleur (with a decorator) we do not use the default behavior
            if (
                method
                in self.registered_app[app_name]["registered_controllers"][controller_name]
            ):
                continue

            (
                request_message_name,
                response_message_name,
            ) = self.register_default_message_from_method(app_name, method, service_instance)

            self.register_default_method(
                app_name, controller_name, method, request_message_name, response_message_name
            )

    def register_default_method(
        self, app_name, controller_name, method, request_message_name, response_message_name
    ):
        """
        If we arrive in this method that mean that the developer use a generation with a default behavior
        We are looking the method passed in argument definition in the know method to define the dictionnary used to generate the protofile
        Basically it's just look if the method as a request or a response stream to specify it to the generation
        """
        if method in KnowMethods.get_methods_no_stream():
            self.registered_app[app_name]["registered_controllers"][controller_name][
                method
            ] = {
                "request": {"is_stream": False, "message": request_message_name},
                "response": {"is_stream": False, "message": response_message_name},
            }

        elif method == KnowMethods.STREAM:
            self.registered_app[app_name]["registered_controllers"][controller_name][
                method
            ] = {
                "request": {"is_stream": False, "message": request_message_name},
                "response": {"is_stream": True, "message": response_message_name},
            }

        else:
            raise RegisterServiceException(
                f"You are registering a service with the method {method} but this methods does not have a decorator and is not in our default supported methods: {KnowMethods.get_as_list()}"
            )

    def register_default_message_from_method(self, app_name, method, service_instance):
        """
        If we arrive in this method that mean that the developer use a generation with a default behavior
        for each king of method we check if this is the method passed as argument and:
            - get the serializer instance associated to the current action/method
            - determine from the serializer and the default method the grpc messages to create
        """

        serializer_instance = self.get_serializer_instance_with_method(
            service_instance, method
        )

        if method in KnowMethods.get_methods_no_custom_messages():
            request_message_name = self.register_serializer_as_message_if_not_exist(
                app_name, serializer_instance, is_request=True
            )
            response_message_name = self.register_serializer_as_message_if_not_exist(
                app_name, serializer_instance, is_request=False
            )
            return request_message_name, response_message_name

        elif method == KnowMethods.LIST:

            return self.register_list_serializer_as_message(
                app_name, service_instance, serializer_instance
            )

        elif method == KnowMethods.RETRIEVE:
            return self.register_retrieve_serializer_as_message(
                app_name, service_instance, serializer_instance
            )

        elif method == KnowMethods.DESTROY:
            return self.register_destroy_serializer_as_message(
                app_name, service_instance, serializer_instance
            )

        elif method == KnowMethods.STREAM:
            return self.register_stream_serializer_as_message(app_name, serializer_instance)

        else:
            raise RegisterServiceException(
                f"You are registering a service with the method {method} but this methods does not have a decorator and is not in our default supported methods: {KnowMethods.get_as_list()}"
            )

    def get_serializer_instance_with_method(self, service_instance, method):
        """
        Assign to the service instance the current action to be able to anticipate case where a service has different serializer class returned
        then call get_serializer_class and return an instance of it for generating message by instrospecting
        """
        service_instance.action = method
        SerializerClass = service_instance.get_serializer_class()

        serializer_instance = SerializerClass()

        # fields = serializer_instance.get_fields()

        return serializer_instance

    def register_list_serializer_as_message(
        self, app_name, service_instance, serializer_instance
    ):
        """
        Method that register a defaut know "list" method in the app proto message
        """
        serializer_name = self.get_message_name_from_field_or_serializer_instance(
            serializer_instance, append_type=False
        )
        child_response_message_name = self.register_serializer_as_message_if_not_exist(
            app_name, serializer_instance, is_request=False
        )

        list_response_field_name = self.get_list_response_field_name_from_serializer_instance(
            serializer_instance
        )

        request_message_name = f"{serializer_name}List{REQUEST_SUFFIX}"
        self.registered_app[app_name]["registered_messages"][request_message_name] = []

        response_message_name = self.register_list_message_of_serializer(
            app_name,
            service_instance,
            base_name=serializer_name,
            list_response_field_name=list_response_field_name,
            child_response_message_name=child_response_message_name,
            is_request=False,
        )

        return request_message_name, response_message_name

    def register_retrieve_serializer_as_message(
        self, app_name, service_instance, serializer_instance, retrieve_field_name=None
    ):
        """
        Method that register a defaut know "retrieve" method in the app proto message
        """
        retrieve_field = self.get_lookup_field_from_serializer(
            serializer_instance, service_instance, retrieve_field_name
        )

        serializer_name = self.get_message_name_from_field_or_serializer_instance(
            serializer_instance, append_type=False
        )

        request_message_name = f"{serializer_name}Retrieve{REQUEST_SUFFIX}"
        self.registered_app[app_name]["registered_messages"][request_message_name] = [
            retrieve_field
        ]

        response_message_name = self.register_serializer_as_message_if_not_exist(
            app_name, serializer_instance, is_request=False
        )

        return request_message_name, response_message_name

    def register_destroy_serializer_as_message(
        self, app_name, service_instance, serializer_instance, destroy_field_name=None
    ):
        """
        Method that register a defaut know "destroy" method in the app proto message
        """

        destroy_field = self.get_lookup_field_from_serializer(
            serializer_instance, service_instance, destroy_field_name
        )

        serializer_name = self.get_message_name_from_field_or_serializer_instance(
            serializer_instance, append_type=False
        )

        request_message_name = f"{serializer_name}Destroy{REQUEST_SUFFIX}"
        self.registered_app[app_name]["registered_messages"][request_message_name] = [
            destroy_field
        ]

        return request_message_name, "google.protobuf.Empty"

    def register_stream_serializer_as_message(self, app_name, serializer_instance):
        """
        Method that register a defaut know "stream" method in the app proto message
        """
        serializer_name = self.get_message_name_from_field_or_serializer_instance(
            serializer_instance, append_type=False
        )

        request_message_name = f"{serializer_name}Stream{REQUEST_SUFFIX}"
        self.registered_app[app_name]["registered_messages"][request_message_name] = []

        response_message_name = self.register_serializer_as_message_if_not_exist(
            app_name, serializer_instance, is_request=False
        )

        return request_message_name, response_message_name

    def get_lookup_field_from_serializer(
        self, serializer_instance, service_instance, field_name=None
    ):
        """
        Find the field associated to the lookup field
        serializer_instance: instance of the serializer used in this service where the lookup field should be present
        service_instance: the service instance itself where we can introspect for lookupfield
        field_name: If e do not want to use the default lookup field of the service but a specific field we just have to specify this params

        return: iterable: [str, <drf.serializers.Field>]
        """
        if field_name is None:
            field_name = service_instance.get_lookup_request_field()

        # TODO - AM - 07/01/2022 - Check if the fied name in the existing field
        if field_name not in serializer_instance.fields:
            raise RegisterServiceException(
                f"Trying to build a Retrieve or Destroy request with retrieve field named: {field_name} but this field is not existing in the serializer: {serializer_instance.__class__.__name__}"
            )

        field_proto_type = self.type_mapping.get(
            serializer_instance.fields[field_name].__class__.__name__,
            "lookup_field_type_not_found",
        )

        # INFO - AM - 07/01/2022 - to match the format retuned by get_fields used for the generation we need to return an iterable with first element field_name and second element the proto type format
        return [field_name, field_proto_type]

    ############################################################################
    #
    # Decorator Registration
    #
    ############################################################################
    def register_custom_action(
        self,
        service_class,
        function_name,
        request=None,
        response=None,
        request_name=None,
        response_name=None,
        request_stream=False,
        response_stream=False,
        use_request_list=False,
        use_response_list=False,
    ):
        app_name = self.get_app_name_from_service_class(service_class)
        # INFO - AM - 14/01/2022 - Initialize the app in the project to be generated as a specific proto file
        if app_name not in self.registered_app:
            self.registered_app[app_name] = {
                "registered_controllers": OrderedDict(),
                "registered_messages": OrderedDict(),
            }

        service_instance = service_class()
        service_name = service_instance.get_service_name()

        (
            request_message_name,
            list_response_field_name,
        ) = self.register_message_for_custom_action(
            app_name,
            service_name,
            function_name,
            request,
            is_request=True,
            message_name=request_name,
        )
        if use_request_list:
            base_name = self.get_base_name_for_list_message(
                service_name, function_name, message_name=request_message_name, is_request=True
            )
            request_message_name = self.register_list_message_of_serializer(
                app_name,
                service_instance,
                base_name=base_name,
                list_response_field_name=list_response_field_name,
                child_response_message_name=request_message_name,
                message_name=request_name,
                is_request=True,
            )

        (
            response_message_name,
            list_response_field_name,
        ) = self.register_message_for_custom_action(
            app_name,
            service_name,
            function_name,
            response,
            is_request=False,
            message_name=response_name,
        )
        if use_response_list:
            base_name = self.get_base_name_for_list_message(
                service_name,
                function_name,
                message_name=response_message_name,
                is_request=False,
            )
            response_message_name = self.register_list_message_of_serializer(
                app_name,
                service_instance,
                base_name=base_name,
                list_response_field_name=list_response_field_name,
                child_response_message_name=response_message_name,
                message_name=response_name,
                is_request=False,
            )
        # INFO - AM - 03/02/3022 - If user specified a response name we use it instead of the automatically generated one
        if response_name:
            response_message_name = response_name

        self.register_method_for_custom_action(
            app_name,
            service_name,
            function_name,
            request_message_name,
            response_message_name,
            request_stream,
            response_stream,
        )

    def register_method_for_custom_action(
        self,
        app_name,
        service_name,
        function_name,
        request_message_name,
        response_message_name,
        request_stream,
        response_stream,
    ):
        controller_name = f"{service_name}Controller"
        if controller_name not in self.registered_app[app_name]["registered_controllers"]:
            self.registered_app[app_name]["registered_controllers"][
                controller_name
            ] = OrderedDict()
        self.registered_app[app_name]["registered_controllers"][controller_name][
            function_name
        ] = {
            "request": {"is_stream": request_stream, "message": request_message_name},
            "response": {"is_stream": response_stream, "message": response_message_name},
        }

    def register_message_for_custom_action(
        self, app_name, service_name, function_name, message, is_request, message_name=None
    ):
        if isinstance(message, list):
            if len(message) == 0:
                return "google.protobuf.Empty", DEFAULT_LIST_FIELD_NAME

            messages_fields = [(item["name"], item["type"]) for item in message]
            if message_name is None:
                message_name = f"{service_name}{function_name}{REQUEST_SUFFIX if is_request else RESPONSE_SUFFIX}"
            self.registered_app[app_name]["registered_messages"][
                message_name
            ] = messages_fields
            return message_name, DEFAULT_LIST_FIELD_NAME

        elif isinstance(message, str):
            # TODO - AM - 27/01/2022 - Maybe check for authorized string like google.protobuf.empty to avoid developer making syntax mistake
            return message, DEFAULT_LIST_FIELD_NAME
        elif inspect.isclass(message) and issubclass(message, BaseSerializer):
            serializer_instance = message()
            list_response_field_name = (
                self.get_list_response_field_name_from_serializer_instance(serializer_instance)
            )
            message_name = self.register_serializer_as_message_if_not_exist(
                app_name, serializer_instance, message_name, is_request=is_request
            )
            return (
                message_name,
                list_response_field_name,
            )
        else:
            raise RegisterServiceException(
                f"{REQUEST_SUFFIX if is_request else RESPONSE_SUFFIX} message for function {function_name} in app {app_name} is not a list, a serializer or a string"
            )

    def get_base_name_for_list_message(
        self, service_name, function_name, message_name, is_request=True
    ):
        suffix = REQUEST_SUFFIX if is_request else RESPONSE_SUFFIX
        list_suffix = f"List{suffix}"
        # INFO - AM - 09/02/2022 - If special protobuf message we have to determine the name for the message
        if self.is_special_protobuf_message(message_name):
            if function_name == KnowMethods.LIST:
                base_name = service_name
            else:
                base_name = f"{service_name}{function_name}"
        # INFO - AM - 09/02/2022 - Avoid duplicate List on message name on list method
        elif function_name == KnowMethods.LIST and list_suffix in message_name:
            base_name = rreplace(message_name, list_suffix, "", 1)
        else:
            base_name = rreplace(message_name, suffix, "", 1)
        return base_name

    def get_app_name_from_service_class(self, service_class):
        return service_class.__module__.split(".")[0]

    def is_special_protobuf_message(self, message_name):
        return message_name.startswith("google.protobuf")