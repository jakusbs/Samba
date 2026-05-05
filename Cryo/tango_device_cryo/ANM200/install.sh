#!/bin/sh

# insert shebang
sed '1s;^;#!/usr/bin/env python3\n;' ANM200.py > ANM200
chmod +x ANM200
mv ANM200 /usr/local/tango_servers
