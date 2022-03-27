#!/usr/bin/env python3

import sys
import re
# import copy
from pathlib import Path
from dataclasses import dataclass
from typing import Dict
from bs4 import BeautifulSoup
from bs4 import Comment as XmlComment


SOURCE = 'source.ts'
TARGET = 'target.ts'
OUTPUT = 'output.ts'


@dataclass
class XmlFile:
    xml: BeautifulSoup
    strings: Dict[str, BeautifulSoup]
    simplified: Dict[str, BeautifulSoup]


def merge(source_ts=SOURCE, target_ts=TARGET, output_ts=OUTPUT):
    with open(source_ts, 'r') as f:
        source = XmlFile(BeautifulSoup(f.read(), 'xml'), {}, {})

    with open(target_ts, 'r') as f:
        target = XmlFile(BeautifulSoup(f.read(), 'xml'), {}, {})

    for src in [source, target]:
        for elem in src.xml.select('context > message'):
            src.strings[elem.source.string] = elem.translation
            src.simplified[re.sub(r'[-_.,:()<>\[\];!?\s]', '', elem.source.string)] = elem.translation

    for key, own in target.strings.items():
        if key in source.strings:
            other = source.strings[key]
            has_numerus = False

            if len(other.select('numerusform')) != len(own.select('numerusform')):
                print("WARNING: string has numerusform in one file but not in other")
                print(f"         '{own.string}' | {other.string}")
                has_numerus = True
            elif len(own.select('numerusform')) > 0:
                own_nums = own.select('numerusform')
                other_nums = other.select('numerusform')
                has_numerus = True

                for a, b in zip(own_nums, other_nums):
                    if b.string and not a.string:
                        a.string = b.string
                    elif b.string and b.string != a.string:
                        comment = XmlComment('alternative translation: ' + b.string)
                        a.insert_before(comment)

                equal = True
                has_empty = False
                for a, b in zip(own_nums, other_nums):
                    if a.string != b.string:
                        equal = False
                    if not a.string or not b.string:
                        has_empty = True

                if equal and getattr(other, 'type', '') != 'unfinished' or getattr(own, 'type', '') != 'unfinished':
                    own['type'] = ''
                    del own['type']

                if has_empty:
                    own['type'] = 'unfinished'
            elif other.string and not own.string:
                own.string = other.string

                if getattr(other, 'type', '') == 'unfinished':
                    own['type'] = 'unfinished'
            elif other.string == own.string:
                if getattr(other, 'type', '') != 'unfinished' or getattr(own, 'type', '') != 'unfinished':
                    own['type'] = ''
                    del own['type']
            elif other.string and other.string != own.string:
                comment = XmlComment('alternative translation: ' + other.string)
                own.insert_before(comment)

            if not has_numerus and not own.string:
                own['type'] = 'unfinished'

    if Path(output_ts).exists():
        print(f"error: output file '{output_ts}' exists")
    else:
        with open(output_ts, 'w') as f:
            f.write(str(target.xml))


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("usage: merge.py SOURCE TARGET OUTPUT")
        print("TODO: automatically merge two directories based on the languages defined in each file")
        print("TODO: mark all strings changed by this tool as 'unfinished'")
        print("TODO: count and report changes")
        print("TODO: merge into Opal")
        sys.exit(0)

    merge(sys.argv[1], sys.argv[2], sys.argv[3])
