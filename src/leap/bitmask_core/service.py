# -*- coding: utf-8 -*-
# service.py
# Copyright (C) 2015 LEAP
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
Bitmask-core Service.
"""
import resource

from twisted.application import service
from twisted.internet import reactor
from twisted.python import log

from leap.bonafide.service import BonafideService

from leap.bitmask_core import mail_services
from leap.bitmask_core import _zmq
from leap.bitmask_core import websocket
from leap.bitmask_core._version import get_versions


class BitmaskBackend(service.MultiService):

    # TODO move configuration methods to a parent class

    def __init__(self, basedir='~/.config/leap'):

        service.MultiService.__init__(self)
        self.init_bonafide()
        self.init_soledad()
        self.init_keymanager()
        self.init_mail()
        self.init_eip()
        self.init_zmq()
        self.init_web()

    def init_bonafide(self):
        bf = BonafideService()
        bf.setName("bonafide")
        bf.setServiceParent(self)
        bf.register_hook('on_passphrase_entry', trigger='soledad')
        bf.register_hook('on_bonafide_auth', trigger='soledad')

    def init_soledad(self):
        sol = mail_services.SoledadService()
        sol.setName("soledad")
        sol.setServiceParent(self)
        sol.register_hook('on_new_soledad_instance', trigger='keymanager')

    def init_keymanager(self):
        km = mail_services.KeymanagerService()
        km.setName("keymanager")
        km.setServiceParent(self)
        km.register_hook('on_new_keymanager_instance', trigger='mail')

    def init_mail(self):
        ms = mail_services.StandardMailService()
        ms.setName("mail")
        ms.setServiceParent(self)

    def init_eip(self):
        pass

    def init_zmq(self):
        zs = _zmq.ZMQDispatcher(self)
        zs.setServiceParent(self)

    def init_web(self):
        ws = websocket.WebSocketsDispatcherService(self)
        ws.setServiceParent(self)

    # General commands for the BitmaskBackend Core Service

    def do_stats(self):
        log.msg('BitmaskCore Service STATS')
        mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return '[+] BitmaskCore: [Mem usage: %s KB]' % (mem / 1024)

    def do_status(self):
        soledad = self.getServiceNamed('soledad')
        keymanager = self.getServiceNamed('keymanager')
        mail = self.getServiceNamed('mail')

        return "[soledad: %s] [keymanager: %s] [mail: %s]" % tuple(
            ["running" if service.running else "stopped" for service in
             soledad, keymanager, mail])

    def do_version(self):
        version = get_versions()['version']
        return "BitmaskCore: %s" % version

    def do_shutdown(self):
        self.stopService()
        reactor.stop()
