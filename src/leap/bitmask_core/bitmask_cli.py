#!/usr/bin/env python
# -*- coding: utf-8 -*-
# bitmask_cli
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
Bitmask Command Line interface: zmq client.
"""
import sys
import getpass
import argparse

from colorama import init as color_init
from colorama import Fore
from twisted.internet import reactor
from txzmq import ZmqEndpoint, ZmqFactory, ZmqREQConnection
import zmq

from leap.bonafide import config

description = (Fore.YELLOW + 'Manage and configure a Bitmask/LEAP Account. '
               'This client connects to a running bitmaskd service.' +
               Fore.RESET)

parser = argparse.ArgumentParser(description=description)
parser.add_argument("--stats", dest="do_stats", action="store_true",
                    help="print service stats")
parser.add_argument("--signup", action="store_true", dest="do_signup",
                    help="signup new user")
parser.add_argument("--auth", dest="do_auth", action="store_true",
                    help="authenticate the passed user")
parser.add_argument("--logout", dest="do_logout", action="store_true",
                    help="logout this user")
parser.add_argument("--username", dest="username",
                    help="user to operate with")
parser.add_argument("--shutdown", dest="do_shutdown", action="store_true",
                    help="shutdown the bonafide service.")

# XXX DEBUG --------------------------------------------------------
parser.add_argument("--debug", dest="do_debug", action="store_true",
                    help="debug command, can be anything")
# ------------------------------------------------------------------
ns = parser.parse_args()


def get_zmq_connection():
    zf = ZmqFactory()
    e = ZmqEndpoint('connect', config.ENDPOINT)
    return ZmqREQConnection(zf, e)


def error(msg):
    print Fore.RED + "[!] %s" % msg + Fore.RESET
    sys.exit(1)

if len(sys.argv) < 2:
    error("Too few arguments. Try %s --help" % sys.argv[0])


if (ns.do_signup or ns.do_auth or ns.do_logout) and not ns.username:
    error(Fore.RED + "Need to pass a username for signup/auth/logout" +
          Fore.RESET)

if ns.username and '@' not in ns.username:
    error(Fore.RED + "Username must be in the form user@provider" + Fore.RESET)


def do_print(stuff):
    print Fore.GREEN + stuff[0] + Fore.RESET


def send_command():

    cb = do_print
    if ns.do_shutdown:
        data = ("shutdown",)

    elif ns.do_stats:
        data = ("stats",)

    elif ns.do_signup:
        passwd = getpass.getpass()
        data = ("signup", ns.username, passwd)

    elif ns.do_auth:
        passwd = getpass.getpass()
        data = ("authenticate", ns.username, passwd)

    elif ns.do_logout:
        passwd = getpass.getpass()
        data = ("logout", ns.username, passwd)

    elif ns.do_debug:
        data = ("get_soledad",)

    s = get_zmq_connection()
    try:
        d = s.sendMsg(*data)
    except zmq.error.Again:
        print Fore.RED + "[ERROR] Server is down" + Fore.RESET
    d.addCallback(cb)
    d.addCallback(lambda x: reactor.stop())


def main():
    color_init()
    reactor.callWhenRunning(reactor.callLater, 0, send_command)
    reactor.run()

if __name__ == "__main__":
    main()

