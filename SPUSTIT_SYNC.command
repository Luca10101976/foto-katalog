#!/bin/bash
cd /Users/lucielejnarova/projekty/foto-katalog
export $(grep -v '^#' .env | xargs)
python3 sync_katalog.py
echo ""
echo "Stiskni Enter pro zavření..."
read
