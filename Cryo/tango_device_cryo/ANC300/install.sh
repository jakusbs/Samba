#!/bin/sh

# insert shebang
sed '1s;^;#!/usr/bin/env python3\n;' ANC300.py > ANC300
chmod +x ANC300
mv ANC300 /usr/local/tango_servers
