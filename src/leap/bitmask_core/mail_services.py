"""
Mail services.

This is quite moving work still.
This should be moved to the different packages when it stabilizes.
"""
import os
from glob import glob
from collections import defaultdict

from twisted.application import service
from twisted.internet import defer
from twisted.python import log

from leap.keymanager import KeyManager
from leap.soledad.client.api import Soledad
from leap.mail.constants import INBOX_NAME
from leap.mail.mail import Account
from leap.mail.imap.service import imap
from leap.mail.incoming.service import IncomingMail, INCOMING_CHECK_PERIOD
from leap.mail.smtp import setup_smtp_gateway

from .uuid_map import UserMap


class Container(object):

    def __init__(self):
        self._instances = defaultdict(None)

    def get_instance(self, key):
        return self._instances.get(key, None)


class HookableService(object):

    """
    This service allows for other services to be notified
    whenever a certain kind of hook is triggered.

    During the service composition, one is expected to register
    a kind of hook with the service that wants to react to the triggering of
    the hook. On that service, the method "notify_hook" will be called,
    which will be in turn dispatched to the method "hook_<name>".

    This is a simplistic implementation for a PoC, we probably will move
    this to another mechanism like leap.common.events, callbacks etc.
    """

    def register_hook(self, kind, trigger):
        if not hasattr(self, 'service_hooks'):
            self.service_hooks = {}
        log.msg("Registering hook %s->%s" % (kind, trigger))
        self.service_hooks[kind] = trigger

    def get_sibling_service(self, kind):
        return self.parent.getServiceNamed(kind)

    def get_hooked_service(self, kind):
        hooks = self.service_hooks
        if kind in hooks:
            return self.get_sibling_service(hooks[kind])

    def notify_hook(self, kind, **kw):
        if kind not in self.subscribed_to_hooks:
            raise RuntimeError(
                "Tried to notify a hook this class is not "
                "subscribed to" % self.__class__)
        getattr(self, 'hook_' + kind)(**kw)


def notify_hooked_services(this_hook, this_service, **data):
    hooked_service = this_service.get_hooked_service(this_hook)
    if hooked_service:
        hooked_service.notify_hook(
            this_hook, **data)


def get_all_soledad_uuids():
    return [os.path.split(p)[-1].split('.db')[0] for p in
            glob(os.path.expanduser('~/.config/leap/soledad/*.db'))]


class SoledadContainer(Container):

    def __init__(self):
        super(SoledadContainer, self).__init__()
        self._usermap = UserMap()

    def add_instance(self, userid, passphrase, uuid=None, token=None):

        if not uuid:
            bootstrapped_uuid = self._usermap.lookup_uuid(userid, passphrase)
            uuid = bootstrapped_uuid
            if not uuid:
                return
        else:
            self._usermap.add(userid, uuid, passphrase)

        user, provider = userid.split('@')

        # TODO automate bootstrapping stuff
        # TODO consolidate canonical factory

        soledad = self._create_soledad_instance(
            uuid, passphrase, '/tmp/soledad',
            'https://goldeneye.cdev.bitmask.net:2323',
            os.path.expanduser(
                '~/.config/leap/providers/%s/keys/ca/cacert.pem' % provider),
            token)

        self._instances[userid] = soledad

        this_hook = 'on_new_soledad_instance'
        data = {'user': userid, 'uuid': uuid, 'token': token,
                'soledad': soledad}
        notify_hooked_services(this_hook, self.service, **data)

    def _create_soledad_instance(self, uuid, passphrase, basedir, server_url,
                                 cert_file, token):
        # setup soledad info
        secrets_path = os.path.join(
            basedir, '%s.secret' % uuid)
        local_db_path = os.path.join(
            basedir, '%s.db' % uuid)

        if token is None:
            syncable = False
            token = ''
        else:
            syncable = True

        return Soledad(
            uuid,
            unicode(passphrase),
            secrets_path=secrets_path,
            local_db_path=local_db_path,
            server_url=server_url,
            cert_file=cert_file,
            auth_token=token,
            defer_encryption=True,
            syncable=syncable)

    def set_remote_auth_token(self, userid, token):
        self.get_instance(userid).token = token

    def set_syncable(self, userid, state):
        # TODO should check that there's a token!
        self.get_instance(userid).set_syncable(bool(state))

    def sync(self, userid):
        self.get_instance(userid).sync()


class SoledadService(service.Service, HookableService):

    subscribed_to_hooks = ('on_bonafide_auth', 'on_passphrase_entry')

    def startService(self):
        print "Starting Soledad Service"
        self._container = SoledadContainer()
        self._container.service = self
        super(SoledadService, self).startService()

    # hooks

    def hook_on_passphrase_entry(self, **kw):
        userid = kw.get('username')
        password = kw.get('password')
        uuid = kw.get('uuid')
        container = self._container
        print "on_passphrase_entry: New Soledad Instance: %s" % userid
        if not container.get_instance(userid):
            container.add_instance(userid, password, uuid=uuid, token=None)

    def hook_on_bonafide_auth(self, **kw):
        userid = kw['username']
        password = kw['password']
        token = kw['token']
        uuid = kw['uuid']
        container = self._container
        if container.get_instance(userid):
            print "Passing a new SRP Token to Soledad: %s" % userid
            container.set_remote_auth_token(userid, token)
            container.set_syncable(userid, True)
        else:
            container.add_instance(userid, password, uuid=uuid, token=token)


class KeymanagerContainer(Container):

    # TODO this should replace code in soledadbootstrapper

    def add_instance(self, userid, token, uuid, soledad):

        # TODO automate bootstrapping stuff
        # TODO consolidate canonical factory

        keymanager = self._create_keymanager_instance(
            userid, token, uuid, soledad)
        # TODO add hook for KEY GENERATION AND SENDING...
        print "Adding Keymanager instance for:", userid
        self._instances[userid] = keymanager

        # TODO use onready-deferreds instead

        this_hook = 'on_new_keymanager_instance'
        data = {'userid': userid, 'soledad': soledad, 'keymanager': keymanager}
        notify_hooked_services(this_hook, self.service, **data)

    def _create_keymanager_instance(self, userid, token, uuid, soledad):
        user, provider = userid.split('@')

        nickserver_uri = "https://nicknym.%s:6425" % provider
        api_uri = "https://api.%s:4430" % provider

        cert_file = os.path.expanduser(
            '~/.config/leap/providers/%s/keys/ca/cacert.pem' % provider)

        km_args = (userid, nickserver_uri, soledad)
        km_kwargs = {
            "token": token,
            "uid": uuid,
            "api_uri": api_uri,
            "api_version": "1",
            "ca_cert_path": cert_file,
            "gpgbinary": "/usr/bin/gpg"
        }
        keymanager = KeyManager(*km_args, **km_kwargs)
        return keymanager


class KeymanagerService(service.Service, HookableService):

    subscribed_to_hooks = ('on_new_soledad_instance',)

    def startService(self):
        print "Starting Keymanager Service"
        self._container = KeymanagerContainer()
        self._container.service = self
        super(KeymanagerService, self).startService()

    # hooks

    def hook_on_new_soledad_instance(self, **kw):
        container = self._container
        user = kw['user']
        token = kw['token']
        uuid = ['uuid']
        soledad = kw['soledad']
        if not container.get_instance(user):
            container.add_instance(user, token, uuid, soledad)



class MailAccountContainer(Container):

    def add_instance(self, userid, soledad, keymanager):
        pass


class StandardMailService(service.MultiService, HookableService):
    """
    A collection of Services.

    This is the parent service, that launches 3 different services that expose
    Encrypted Mail Capabilities on specific ports:

        - SMTP service, on port 2013
        - IMAP service, on port 1984
        - The IncomingMail Service, which doesn't listen on any port, but
          watches and processes the Incoming Queue and saves the processed mail
          into the matching INBOX.
    """

    # TODO factor out Mail Service to inside mail package.

    subscribed_to_hooks = ('on_new_keymanager_instance',)

    def __init__(self):
        self._soledad_sessions = {}
        self._keymanager_sessions = {}
        self._imap_tokens = {}
        self._active_user = None
        super(StandardMailService, self).__init__()
        self.initializeChildrenServices()

    def initializeChildrenServices(self):
        self.addService(IMAPService(self._soledad_sessions))
        self.addService(IncomingMailService(self))
        self.addService(SMTPService())

    def startService(self):
        print "Starting Mail Service..."
        self._container = MailAccountContainer()
        self._container.service = self
        super(StandardMailService, self).startService()

    def stopService(self):
        super(StandardMailService, self).stopService()

    def startInstance(self, userid, soledad, keymanager):
        self._soledad_sessions[userid] = soledad
        self._keymanager_sessions[userid] = keymanager

        smtp = self.getServiceNamed('smtp')
        smtp.startInstance(keymanager, userid)

        incoming = self.getServiceNamed('incoming_mail')
        incoming.startInstance(userid)

        def registerIMAPToken(token):
            self._imap_tokens[userid] = token
            self._active_user = userid
            return token

        d = soledad.get_or_create_service_token('imap')
        d.addCallback(registerIMAPToken)
        return d

    def stopInstance(self):
        pass

    # hooks

    def hook_on_new_keymanager_instance(self, **kw):
        # XXX we can specify this as a waterfall, or just AND the two
        # conditions.
        userid = kw['userid']
        soledad = kw['soledad']
        keymanager = kw['keymanager']
        self.startInstance(userid, soledad, keymanager)

    # commands

    def do_status(self):
        return 'mail: %s' % 'running' if self.running else 'disabled'

    def get_imap_token(self):
        # TODO this should have some kind of previous authentication with
        # whatever communication channel we're using.
        active_user = self._active_user
        if not active_user:
            return defer.succeed('NO ACTIVE USER')
        token = self._imap_tokens.get(active_user)
        return defer.succeed("IMAP TOKEN (%s): %s" % (active_user, token))

    # access to containers

    def get_soledad_session(self, userid):
        return self._soledad_sessions.get(userid)

    def get_keymanager_session(self, userid):
        return self._keymanager_sessions.get(userid)


class IMAPService(service.Service):

    name = 'imap'

    def __init__(self, soledad_sessions):
        port, factory = imap.run_service(soledad_sessions)

        self._port = port
        self._factory = factory
        self._soledad_sessions = soledad_sessions
        super(IMAPService, self).__init__()

    def startService(self):
        print "Starting IMAP Service"
        super(IMAPService, self).startService()

    def stopService(self):
        self._port.stopListening()
        self._factory.doStop()
        super(IMAPService, self).stopService()


class SMTPService(service.Service):

    name = 'smtp'

    # TODO --- this needs to allow authentication,
    # to be able to expose the SAME service for different
    # accounts.

    # TODO -- the offline service (ie, until BONAFIDE REMOTE
    # has been authenticated) should expose a dummy SMTP account.

    def __init__(self):
        super(SMTPService, self).__init__()
        self._instances = {}

    def startService(self):
        print "Starting dummy SMTP Service"
        super(SMTPService, self).startService()

    def stopService(self):
        # TODO cleanup all instances
        super(SMTPService, self).stopService()

    # Individual accounts

    def startInstance(self, keymanager, userid):
        # TODO automate bootstrapping stuff
        # TODO consolidate canonical factory
        user, provider = userid.split('@')
        host = 'cowbird.cdev.bitmask.net'
        remote_port = 465
        client_cert_path = os.path.expanduser(
            '~/.config/leap/providers/dev.bitmask.net/'
            'keys/client/stmp_%s.pem' % user)
        service, port = setup_smtp_gateway(
            port=2013,
            userid=userid,
            keymanager=keymanager,
            smtp_host=host,
            smtp_port=remote_port,
            smtp_cert=unicode(client_cert_path),
            smtp_key=unicode(client_cert_path),
            encrypted_only=False)
        self._instances[userid] = service, port

    def stopInstance(self, userid):
        port, factory = self._instances[userid]
        port.stopListening()
        factory.doStop()


class IncomingMailService(service.Service):

    name = 'incoming_mail'

    def __init__(self, mail_service):
        super(IncomingMailService, self).__init__()
        self._mail = mail_service
        self._instances = {}

    def startService(self):
        print "Starting IncomingMail Service"
        super(IncomingMailService, self).startService()

    def stopService(self):
        super(IncomingMailService, self).stopService()

    # Individual accounts

    # TODO IncomingMail *IS* already a service.
    # I think we should better model the current Service
    # as a startInstance inside a container, and get this
    # multi-tenant service inside the leap.mail.incoming.service.
    # ... or just simply make it a multiService and set per-user
    # instances as Child of this parent.

    def startInstance(self, userid):
        soledad = self._mail.get_soledad_session(userid)
        keymanager = self._mail.get_keymanager_session(userid)

        print "Starting instance for %s" % userid
        self._start_incoming_mail_instance(
            keymanager, soledad, userid)

    def stopInstance(self, userid):
        # TODO toggle offline!
        pass

    def _start_incoming_mail_instance(self, keymanager, soledad,
                                      userid, start_sync=True):

        def setUpIncomingMail(inbox):
            incoming_mail = IncomingMail(
                keymanager, soledad,
                inbox, userid,
                check_period=INCOMING_CHECK_PERIOD)
            return incoming_mail

        def registerInstance(incoming_instance):
            self._instances[userid] = incoming_instance
            if start_sync:
                incoming_instance.startService()

        acc = Account(soledad)
        d = acc.callWhenReady(
            lambda _: acc.get_collection_by_mailbox(INBOX_NAME))
        d.addCallback(setUpIncomingMail)
        d.addCallback(registerInstance)
        d.addErrback(lambda f: log.err(f))
        return d
