#!/bin/sh

# insert shebang
sed '1s;^;#!/usr/bin/env python3\n;' PyKeithley2.py > PyKeithley2
chmod +x PyKeithley2
mv PyKeithley2 /usr/local/tango_servers
