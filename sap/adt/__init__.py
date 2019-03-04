"""Base classes for ADT functionality modules"""

import re
import collections
import requests
from requests.auth import HTTPBasicAuth

from sap import get_logger
from sap.errors import SAPCliError
from sap.adt.errors import HTTPRequestError

import sap.adt.marshalling
from sap.adt.annotations import xml_attribute, xml_element

from sap.adt.aunit import AUnit  # noqa: F401


def mod_log():
    """ADT Module logger"""

    return get_logger()


class Connection:
    """ADT Connection for HTTP communication built on top Python requests.
    """

    def __init__(self, host, client, user, password, port=None, ssl=True):
        """Parameters:
            - host: string host name
            - client: string SAP client
            - user: string user name
            - password: string user password
            - port: string TCP/IP port for ADT
                    (default 80 or 443 - it depends on the parameter ssl)
            - ssl: boolean to switch between http and https
        """

        if ssl:
            protocol = 'https'
            if port is None:
                port = '443'
        else:
            protocol = 'http'
            if port is None:
                port = '80'

        self._adt_uri = 'sap/bc/adt'
        self._base_url = '{protocol}://{host}:{port}/{adt_uri}'.format(
            protocol=protocol, host=host, port=port, adt_uri=self._adt_uri)
        self._query_args = 'sap-client={client}&saml2=disabled'.format(
            client=client)
        self._user = user
        self._auth = HTTPBasicAuth(user, password)
        self._session = None

    @property
    def user(self):
        """Connected user"""

        return self._user

    @property
    def uri(self):
        """ADT path for building URLs (e.g. sap/bc/adt)"""

        return self._adt_uri

    def _build_adt_url(self, adt_uri):
        """Creates complete URL from a fragment of ADT URI
           where the fragment usually refers to an ADT object
        """

        return '{base_url}/{adt_uri}?{query_args}'.format(
            base_url=self._base_url, adt_uri=adt_uri,
            query_args=self._query_args)

    @staticmethod
    def _execute_with_session(session, method, url, params=None, headers=None, body=None):
        """Executes the given URL using the given method in
           the common HTTP session.
        """

        req = requests.Request(method.upper(), url, params=params, data=body, headers=headers)
        req = session.prepare_request(req)

        mod_log().info('Executing %s %s', method, url)
        res = session.send(req)

        mod_log().debug('Response %s %s:\n++++\n%s\n++++', method, url, res.text)

        if res.status_code >= 400:
            raise HTTPRequestError(req, res)

        return res

    def _get_session(self):
        """Returns the working HTTP session.
           The session's cookies are populated by executing a dummy GET which
           also retrieves X-CSRF-Token.
        """

        if self._session is None:
            self._session = requests.Session()
            self._session.auth = self._auth

            url = self._build_adt_url('core/discovery')

            response = Connection._execute_with_session(self._session, 'GET', url, headers={'x-csrf-token': 'Fetch'})

            self._session.headers.update({'x-csrf-token': response.headers['x-csrf-token']})

        return self._session

    def execute(self, method, adt_uri, params=None, headers=None, body=None):
        """Executes the given ADT URI as an HTTP request and returns
           the requests response object
        """

        session = self._get_session()

        url = self._build_adt_url(adt_uri)

        return Connection._execute_with_session(session, method, url, params=params, headers=headers, body=body)

    def get_text(self, relativeuri):
        """Executes a GET HTTP request with the headers Accept = text/plain.
        """

        return self.execute('GET', relativeuri, headers={'Accept': 'text/plain'}).text


LOCK_ACCESS_MODE_MODIFY = 'MODIFY'


def lock_params(access_mode):
    """Returns parameters for Action Lock"""

    return {'_action': 'LOCK', 'accessMode': access_mode}


def unlock_params(lock_handle):
    """Returns parameters for Action Unlock"""

    return {'_action': 'UNLOCK', 'lockHandle': lock_handle}


def activation_params():
    """Returns parameters for Activation of object"""

    return {'method': 'activate', 'preauditRequested': 'true'}


def create_params(corrnr):
    """Returns parameters for Creation of object"""

    if corrnr is None:
        return None

    return {'corrnr': corrnr}


class ADTObjectType:
    """Common ADT object type attributes.
    """

    def __init__(self, code, basepath, xmlnamespace, mimetype, typeuris, xmlname):
        """Parameters:
            - code: ADT object code
            - basepath:
            - xmlnamespace: a tuple where the first item is a nick and
                            the second item is actually the namespace URI
            - mimetype: object MIME type
            - typeuris: patterns for the object format URL (text, xml, ...)
            - xmlname: something from ADT ;)
        """

        self._code = code
        self._basepath = basepath
        self._xmlnamespace = xmlnamespace
        self._mimetype = mimetype
        self._typeuris = typeuris
        self._xmlname = xmlname

    @property
    def code(self):
        """ADT object code"""

        return self._code

    @property
    def basepath(self):
        """Object fragment of ADT URL"""

        return self._basepath

    @property
    def mimetype(self):
        """ADT object MIME type"""

        return self._mimetype

    @property
    def xmlnamespace(self):
        """A tuple (namespace nick, namespace URL)"""

        return self._xmlnamespace

    @property
    def xmlname(self):
        """XML element name"""

        return self._xmlname

    def get_uri_for_type(self, mimetype):
        """Returns and an ADT URL fragment for the given MIME type.
        """

        try:
            return '/' + self._typeuris[mimetype]
        except KeyError:
            raise SAPCliError('Object {type} does not support plain \'text\' format')


class OrderedClassMembers(type):
    """MetaClass to preserve get order of member declarations
       to serialize the XML elements in the expected order.
    """

    @classmethod
    # pylint: disable=unused-argument
    def __prepare__(mcs, name, bases):
        return collections.OrderedDict()

    def __new__(mcs, name, bases, classdict):
        members = []

        if bases:
            parent = bases[-1]
            if hasattr(parent, '__ordered__'):
                members.extend(parent.__ordered__)

        members.extend([key for key in classdict.keys()
                        if key not in ('__module__', '__qualname__')])

        classdict['__ordered__'] = members

        return type.__new__(mcs, name, bases, classdict)


class ADTCoreData:
    """Common SAP object attributes.
    """

    class Reference(metaclass=OrderedClassMembers):
        """Package Reference
        """

        def __init__(self, name=None):
            self._name = name

        @xml_attribute('adtcore:name')
        def name(self):
            """package reference name """

            return self._name

        @name.setter
        def name(self, value):
            """sets package reference name"""

            self._name = value

    # pylint: disable=too-many-arguments
    def __init__(self, package=None, description=None, language=None,
                 master_language=None, master_system=None, responsible=None,
                 package_reference=None):
        self._package = package
        self._description = description
        self._language = language
        self._master_language = master_language
        self._master_system = master_system
        self._responsible = responsible
        self._package_reference = ADTCoreData.Reference(name=package_reference)

    @property
    def package(self):
        """ABAP development package (DEVC)"""

        return self._package

    @property
    def description(self):
        """Object description"""

        return self._description

    @description.setter
    def description(self, value):
        """Object description setter"""

        self._description = value

    @property
    def language(self):
        """Language"""

        return self._language

    @property
    def master_language(self):
        """Original (master) language"""

        return self._master_language

    @property
    def master_system(self):
        """Original (master) system"""

        return self._master_system

    @master_system.setter
    def master_system(self, value):
        """Original (master) system setter"""

        self._master_system = value

    @property
    def responsible(self):
        """Object responsible person"""

        return self._responsible

    @responsible.setter
    def responsible(self, value):
        """Object responsible person setter"""

        self._responsible = value

    @property
    def package_reference(self):
        """The object's package reference"""

        return self._package_reference

    @package_reference.setter
    def package_reference(self, value):
        """Set the object's package reference"""

        self._package_reference = value


class ADTObject(metaclass=OrderedClassMembers):
    """Abstract base class for ADT objects
    """

    def __init__(self, connection, name, metadata=None):
        """Parameters:
            - connection: ADT.Connection
            - name: string name
            - metadata: ADTCoreData
        """

        self._connection = connection
        self._name = name

        self._metadata = metadata if metadata is not None else ADTCoreData()

        self._lock = None

    @property
    def coredata(self):
        """ADT Core Data"""

        return self._metadata

    @property
    def connection(self):
        """ADT Connection"""

        return self._connection

    @property
    def objtype(self):
        """ADT type definition"""

        # pylint: disable=no-member
        return self.__class__.OBJTYPE

    @property
    def package(self):
        """ABAP development package"""

        return self._metadata.package

    @xml_attribute('adtcore:description')
    def description(self):
        """SAP object description"""

        return self._metadata.description

    @description.setter
    def description(self, value):
        """SAP object description setter"""

        self._metadata.description = value

    @xml_attribute('adtcore:language')
    def language(self):
        """SAP object language"""

        return self._metadata.language

    @xml_attribute('adtcore:name')
    def name(self):
        """SAP Object name"""

        return self._name

    @xml_attribute('adtcore:masterLanguage')
    def master_language(self):
        """SAP object original (master) language"""

        return self._metadata.master_language

    @xml_attribute('adtcore:masterSystem')
    def master_system(self):
        """SAP object original (master) system"""

        return self._metadata.master_system

    @xml_attribute('adtcore:responsible')
    def responsible(self):
        """SAP object responsible"""

        return self._metadata.responsible

    @property
    def uri(self):
        """ADT object URL fragment"""

        # pylint: disable=no-member
        return self.objtype.basepath + '/' + self.name.lower()

    @property
    def text(self):
        """Downloads text representation of the SAP Object
           if the MIME Type 'text/plain'.
        """

        text_uri = self.objtype.get_uri_for_type('text/plain')

        return self._connection.get_text('{objuri}{text_uri}'.format(
            objuri=self.uri, text_uri=text_uri))

    @xml_element('adtcore:packageRef')
    def reference(self):
        """The object's package reference"""

        return self._metadata.package_reference

    def create(self, corrnr=None):
        """Creates ADT object
        """

        marshal = sap.adt.marshalling.Marshal()
        xml = marshal.serialize(self)

        return self._connection.execute(
            'POST',
            self.objtype.basepath,
            headers={'Content-Type': self.objtype.mimetype},
            params=create_params(corrnr),
            body=xml)

    def lock(self):
        """Locks the object"""

        if self._lock is not None:
            raise SAPCliError(f'Object {self.uri}: already locked')

        resp = self._connection.execute(
            'POST',
            self.uri,
            params=lock_params(LOCK_ACCESS_MODE_MODIFY),
            headers={
                'X-sap-adt-sessiontype': 'stateful',
                'Accept': ', '.join([
                    'application/vnd.sap.as+xml;charset=UTF-8;dataname=com.sap.adt.lock.result;q=0.8',
                    'application/vnd.sap.as+xml;charset=UTF-8;dataname=com.sap.adt.lock.result2;q=0.9'
                ])
            }
        )

        if 'dataname=com.sap.adt.lock.Result' not in resp.headers['Content-Type']:
            raise SAPCliError(f'Object {self.uri}: lock response does not have lock result\n' + resp.text)

        mod_log().debug(resp.text)

        # TODO: check encoding
        self._lock = re.match('.*<LOCK_HANDLE>(.*)</LOCK_HANDLE>.*', resp.text)[1]
        mod_log().debug('LockHandle=%s', self._lock)

    def unlock(self):
        """Locks the object"""

        if self._lock is None:
            raise SAPCliError(f'Object {self.uri}: not locked')

        self._connection.execute(
            'POST',
            self.uri,
            params=unlock_params(self._lock),
            headers={
                'X-sap-adt-sessiontype': 'stateful',
            }
        )

        self._lock = None

    def activate(self):
        """Activate the object"""

        # pylint: disable=no-member
        request = f'''<?xml version="1.0" encoding="UTF-8"?>
<adtcore:objectReferences xmlns:adtcore="http://www.sap.com/adt/core">
<adtcore:objectReference adtcore:uri="/{self.connection.uri}/{self.uri}" adtcore:name="{self.name.upper()}"/>
</adtcore:objectReferences>'''

        resp = self._connection.execute(
            'POST',
            'activation',
            params=activation_params(),
            headers={
                'Accept': 'application/xml',
                'Content-Type': 'application/xml'
            },
            body=request
        )

        if resp.text:
            raise SAPCliError(f'Could not activate the object {self.name}: {resp.text}')


class Program(ADTObject):
    """ABAP Report/Program
    """

    OBJTYPE = ADTObjectType(
        'PROG/P',
        'programs/programs',
        ('program', 'http://www.sap.com/adt/programs/programs'),
        'application/vnd.sap.adt.programs.programs.v2+xml',
        {'text/plain': 'source/main'},
        'abapProgram'
    )

    def __init__(self, connection, name, package=None, metadata=None):
        super(Program, self).__init__(connection, name, metadata)

        self._metadata.package_reference.name = package

    # pylint: disable=no-self-use
    @xml_attribute('adtcore:version')
    def active(self):
        """Version in regards of activation"""

        return "active"

    def change_text(self, content):
        """Changes the source code"""

        text_uri = self.objtype.get_uri_for_type('text/plain')

        resp = self._connection.execute(
            'PUT', self.uri + text_uri,
            params={'lockHandle': self._lock},
            headers={
                'Content-Type': 'text/plain; charset=utf-8'},
            body=content)

        mod_log().debug("Change text response status: %i", resp.status_code)


class Class(ADTObject):
    """ABAP OO Class
    """

    OBJTYPE = ADTObjectType(
        'CLAS/OC',
        'oo/classes',
        ('class', 'http://www.sap.com/adt/oo/classes'),
        'application/vnd.sap.adt.oo.classes.v2+xml',
        {'text/plain': 'source/main'},
        'abapClass'
    )

    class SuperClass(metaclass=OrderedClassMembers):
        """Super Class reference
        """

        def __init__(self, name=None):
            self._name = name

        @xml_attribute('adtcore:name')
        def name(self):
            """Application component name
            """

            return self._name

    class Include(metaclass=OrderedClassMembers):
        """Class includes"""

        def __init__(self, adt_name, adt_type, include_type):
            self._adt_name = adt_name
            self._adt_type = adt_type
            self._include_type = include_type

        @staticmethod
        def test_classes():
            """Include for Test Class"""

            return Class.Include('CLAS/OC', 'CLAS/OC', 'testclasses')

        @xml_attribute('adtcore:name')
        def adt_name(self):
            """ADT Object name"""

            return self._adt_name

        @xml_attribute('adtcore:type')
        def adt_type(self):
            """ADT Object Type name"""

            return self._adt_type

        @xml_attribute('class:includeType')
        def include_type(self):
            """ADT Class include type"""

            return self._include_type

    def __init__(self, connection, name, package=None, metadata=None):
        super(Class, self).__init__(connection, name, metadata)

        self._metadata.package_reference.name = package
        self._superclass = Class.SuperClass()

    # pylint: disable=no-self-use
    @xml_attribute('class:final')
    def final(self):
        """Final flag"""

        return "true"

    # pylint: disable=no-self-use
    @xml_attribute('class:visibility')
    def visibility(self):
        """Visibility flag"""

        return "public"

    # pylint: disable=no-self-use
    @xml_element('class:include')
    def include(self):
        """Class include"""

        return Class.Include.test_classes()

    @xml_element('class:superClassRef')
    def super_class(self):
        """Super Class reference"""

        return self._superclass

    def change_text(self, content):
        """Changes the source code"""

        text_uri = self.objtype.get_uri_for_type('text/plain')

        resp = self._connection.execute(
            'PUT', self.uri + text_uri,
            params={'lockHandle': self._lock},
            headers={
                'Accept': 'text/plain',
                'Content-Type': 'text/plain; charset=utf-8'},
            body=content)

        mod_log().debug("Change text response status: %i", resp.status_code)


class Package(ADTObject):
    """ABAP Package - Development class - DEVC"""

    OBJTYPE = ADTObjectType(
        'DEVC/K',
        'packages',
        ('pak', 'http://www.sap.com/adt/packages'),
        'application/vnd.sap.adt.packages.v1+xml',
        {},
        'package'
    )

    class SuperPackage(metaclass=OrderedClassMembers):
        """Super Package
        """

        def __init__(self, name=None):
            self._name = name

        @xml_attribute('adtcore:name')
        def name(self):
            """super package name
            """

            return self._name

        @name.setter
        def name(self, value):
            """super package name
            """

            self._name = value

    class SoftwareComponent(metaclass=OrderedClassMembers):
        """SAP Software component.
        """

        def __init__(self, name=None):
            self._name = name

        @xml_attribute('pak:name')
        def name(self):
            """Software component name
            """

            return self._name

    class ApplicationComponent(metaclass=OrderedClassMembers):
        """Application component.
        """

        def __init__(self, name=None):
            self._name = name

        @xml_attribute('pak:name')
        def name(self):
            """Application component name
            """

            return self._name

    class Attributes(metaclass=OrderedClassMembers):
        """SAP Package attributes.
        """

        def __init__(self, name=None):
            self._package_type = name

        @xml_attribute('pak:packageType')
        def package_type(self):
            """The Package's type
            """

            return self._package_type

        @package_type.setter
        def package_type(self, value):
            """The Package's type setter
            """

            self._package_type = value

    class Transport(metaclass=OrderedClassMembers):
        """SAP Package transport details.
        """

        class Layer(metaclass=OrderedClassMembers):
            """SAP Software component.
            """

            def __init__(self, name=None):
                self._name = name

            @xml_attribute('pak:name')
            def name(self):
                """Software component name
                """

                return self._name

        def __init__(self):
            self._software_component = Package.SoftwareComponent()
            self._layer = Package.Transport.Layer()

        @xml_element('pak:softwareComponent')
        def software_component(self):
            """The Package's software component
            """

            return self._software_component

        @software_component.setter
        def software_component(self, value):
            """The Package's software component setter
            """

            self._software_component = value

        @xml_element('pak:transportLayer')
        def transport_layer(self):
            """The Package's transport layer
            """

            return self._layer

        @transport_layer.setter
        def transport_layer(self, value):
            """Set's the transport layer"""

            self._layer = value

    def __init__(self, connection, name, metadata=None):
        super(Package, self).__init__(connection, name, metadata)

        self._superpkg = Package.SuperPackage()
        self._transport = Package.Transport()
        self._attributes = Package.Attributes()
        self._metadata.package_reference.name = name
        self._appcomp = None

    # pylint: disable=no-self-use
    @xml_attribute('adtcore:version')
    def active(self):
        """Version in regards of activation"""

        return "active"

    @xml_element('pak:attributes')
    def attributes(self):
        """The package's attributes.
        """
        return self._attributes

    @xml_element('pak:superPackage')
    def super_package(self):
        """The package's super package.
        """

        return self._superpkg

    @xml_element('pak:applicationComponent')
    # pylint: disable=no-self-use
    def app_component(self):
        """The package's application component
        """

        return self._appcomp

    @xml_element('pak:transport')
    def transport(self):
        """The package's transport configuration.
        """

        return self._transport

    @xml_element('pak:translation')
    # pylint: disable=no-self-use
    def translation(self):
        """The package's translation flag
        """

        return None

    @xml_element('pak:useAccesses')
    # pylint: disable=no-self-use
    def use_accesses(self):
        """The package's Use Accesses
        """

        return None

    @xml_element('pak:packageInterfaces')
    # pylint: disable=no-self-use
    def package_interfaces(self):
        """The package's Interfaces
        """

        return None

    @xml_element('pak:subPackages')
    # pylint: disable=no-self-use
    def sub_packages(self):
        """The package's sub-packages
        """

        return None

    def set_package_type(self, package_type):
        """Changes the Package's type
        """

        self._attributes.package_type = package_type

    def set_software_component(self, name):
        """Changes the Package's software component
        """

        self._transport.software_component = Package.SoftwareComponent(name)

    def set_transport_layer(self, name):
        """Changes the Package's transport layer
        """

        self._transport.transport_layer = Package.Transport.Layer(name)

    def set_app_component(self, name):
        """Changes the Package's software component
        """

        self._appcomp = Package.ApplicationComponent(name)
