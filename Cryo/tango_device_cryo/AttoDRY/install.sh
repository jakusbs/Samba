#!/bin/sh

# insert shebang
sed '1s;^;#!/usr/bin/env python3\n;' AttoDRY.py > AttoDRY
chmod +x AttoDRY
mv AttoDRY /usr/local/tango_servers
cp AttoDRYThreadDaemon.py /usr/local/tango_servers/
cp AttoDRYCheck.py /usr/local/tango_servers/
