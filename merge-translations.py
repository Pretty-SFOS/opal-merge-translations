#!/usr/bin/env python3

import argparse
from collections import defaultdict
import sys
import textwrap
import glob
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple
from bs4 import BeautifulSoup
from bs4 import Comment as XmlComment


@dataclass
class Language:
    lang: str
    area: str

    @property
    def empty(self) -> bool:
        return not self.lang

    @staticmethod
    def from_str(string) -> 'Language':
        if match := re.match(r'^(?P<lang>[a-z]{2})[_-](?P<area>[A-Z]{2})$', string):
            lang = match.group('lang')
            area = match.group('area')
        elif match := re.match(r'^(?P<lang>[a-zA-Z]{2})$', string):
            lang = match.group('lang').lower()
            area = ''
        else:
            lang = ''
            area = ''

        return Language(lang, area)

    def is_subset_of(self, other) -> bool:
        if self.lang != other.lang:
            return False

        if self.area and other.area and self.area != other.area:
            return False

        return True

    def __str__(self) -> str:
        if self.area:
            return f'{self.lang.lower()}_{self.area.upper()}'
        else:
            return self.lang.lower()

    def __hash__(self) -> int:
        return hash(str(self))


@dataclass
class TsFile:
    path: Path
    parsed: BeautifulSoup
    strings: Dict[str, BeautifulSoup]
    simplified: Dict[str, BeautifulSoup]
    language: Language

    @staticmethod
    def from_disk(path) -> 'TsFile':
        if not Path(path).is_file():
            raise FileNotFoundError()

        with open(path, 'r') as f:
            parsed = BeautifulSoup(f.read(), 'xml')

        strings = {}
        simplified = {}
        for elem in parsed.select('context > message'):
            string = elem.source.string

            if string is None or str(string) == '':
                continue

            strings[elem.source.string] = elem.translation
            simplified[re.sub(r'[-_.,:()<>\[\];!?\s]', '', elem.source.string)] = elem.translation

        language = None
        if elem := parsed.find('TS', recursive=False):
            if lang_str := getattr(elem, 'language', ''):
                language = Language.from_str(lang_str)

        if not language:
            if match := re.match(r'^.*?-(?P<lang_str>([a-z]{2})([-_][A-Z]{2})?)\.[tT][sS]$', str(path)):
                language = Language.from_str(match.group('lang_str'))

        if not language:
            msg = f'cannot extract language from file "{path}"'
            raise RuntimeError(msg)

        return TsFile(Path(path), parsed, strings, simplified, language)

    def __lt__(self, other: 'TsFile') -> bool:  # required for sorting
        return str(self.path) < str(other.path)

    def __hash__(self) -> int:
        return hash(str(self.path))


@dataclass
class TsDirectory:
    directory: Path
    files: Dict[Language, TsFile]

    class DuplicateLanguageError(Exception):
        pass

    @staticmethod
    def from_disk(path) -> 'TsDirectory':
        path = Path(path)
        if not path.is_dir():
            msg = f'directory "{path}" not found'
            raise FileNotFoundError(msg)

        files = {}
        for i in glob.iglob(str(path / '*.[tT][sS]'), recursive=False):
            try:
                ts = TsFile.from_disk(i)
            except RuntimeError:
                print(f'warning: skipped file "{i}"')
                continue

            if ts.language in files:
                msg = f'language "{ts.language}" is already registered: {ts.path}, {files[ts.language].path}'
                raise TsDirectory.DuplicateLanguageError(msg)

            files[ts.language] = ts

        return TsDirectory(path, files)


class Merger:
    def __init__(self, args):
        self.args = args

        self.sources: List[TsDirectory] = []
        self.target: TsDirectory = None

        self.pairs: Dict[TsFile, List[TsFile]] = defaultdict(list)
        self.handled_files: List[TsFile] = []
        self.no_match: List[TsFile] = []
        self.not_handled: List[TsFile] = []

        self.overall_changes: int = 0
        self.overall_alternatives: Dict[TsFile, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        self.overall_alternatives_count: int = 0

        self.output = args.output
        self.force = args.force

        if self.output and Path(self.output).exists() and not self.force:
            msg = f'output directory exists: "{self.output}" (use -f to overwrite)'
            raise FileExistsError(msg)
        elif not self.output and not self.force:
            msg = f'use -f to overwrite original target files ("{args.target[0]}")'
            raise FileExistsError(msg)
        elif self.output:
            self.output = Path(self.output)
            self.output.mkdir(parents=True, exist_ok=True)
        else:
            self.output = Path(args.target[0])

            if not self.output.is_dir():
                msg = f'target directory not found: "{self.output}"'
                raise FileNotFoundError(msg)

    @staticmethod
    def run(args) -> 'Merger':
        merger = Merger(args)
        merger._collect()
        merger._match()
        merger._merge()
        merger._save()
        merger._report()
        return merger

    def _collect(self):
        print('collecting files...')

        try:
            self.target = TsDirectory.from_disk(args.target[0])
        except FileNotFoundError:
            print('error: target directory not found')
            raise FileNotFoundError()
        except TsDirectory.DuplicateLanguageError:
            print('error: target directory contains invalid files')

        try:
            for i in args.source:
                self.sources.append(TsDirectory.from_disk(i))
        except FileNotFoundError:
            print('error: source directory not found')
            raise

    def _match(self):
        print('matching languages...')

        for t in self.target.files.values():
            for s in self.sources:
                for s_file in s.files.values():
                    if s_file.language.is_subset_of(t.language):
                        self.pairs[t].append(s_file)
                        self.handled_files.append(s_file)

            print(f'{t.language}: {t.path}')

            for i in self.pairs[t]:
                print(f'- {i.path} ({i.language})')

            if not self.pairs[t]:
                print('- no matching files found')
                self.no_match.append(t)

        for s in self.sources:
            for s_file in s.files.values():
                if s_file not in self.handled_files:
                    self.not_handled.append(s_file)

    def _do_merge_pair(self, source: TsFile, target: TsFile) -> Tuple[int, Dict[str, List[str]]]:
        changes = 0
        alternatives = defaultdict(list)

        for key, own in target.strings.items():
            if key in source.strings:
                other = source.strings[key]
                has_numerus = False

                if len(other.select('numerusform')) != len(own.select('numerusform')):
                    print("WARNING: string has numerusform in one file but not in other")
                    print(f"         '{own.string}' | '{other.string}'")
                    has_numerus = True
                elif len(own.select('numerusform')) > 0:
                    own_nums = own.select('numerusform')
                    other_nums = other.select('numerusform')
                    has_numerus = True

                    for a, b in zip(own_nums, other_nums):
                        if b.string and not a.string:
                            a.string = b.string
                            changes += 1
                        elif b.string and b.string != a.string:
                            comment = XmlComment('alternative translation: ' + b.string)
                            a.insert_before(comment)
                            # changes += 1

                            if alternatives[key]:
                                alternatives[key].append(b.string)
                            else:
                                alternatives[key] += ['** ' + a.string, b.string]

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
                    changes += 1

                    if getattr(other, 'type', '') == 'unfinished':
                        own['type'] = 'unfinished'
                elif other.string == own.string:
                    if getattr(other, 'type', '') != 'unfinished' or getattr(own, 'type', '') != 'unfinished':
                        own['type'] = ''
                        del own['type']
                    # changes += 1
                elif other.string and other.string != own.string:
                    comment = XmlComment('alternative translation: ' + other.string)
                    own.insert_before(comment)
                    # changes += 1

                    if alternatives[key]:
                        alternatives[key].append(other.string)
                    else:
                        alternatives[key] += ['** ' + own.string, other.string]
                if not has_numerus and not own.string:
                    own['type'] = 'unfinished'

        return (changes, alternatives)

    def _merge(self):
        print('merging files...')

        for target, matches in self.pairs.items():
            print(f'{target.language}: {target.path}')
            total_changes = 0
            total_ambiguous = 0

            for source in matches:
                changes, alternatives = self._do_merge_pair(source, target)
                alternatives_count = len(alternatives.items())
                total_changes += changes
                total_ambiguous += alternatives_count

                for key, alts in alternatives.items():
                    self.overall_alternatives[target][key] = list(set(self.overall_alternatives[target][key] + alts))

                print(f'- {source.path} ({source.language}) [+{changes} / {alternatives_count}]')

            print(f'- total changes: +{total_changes} / {total_ambiguous}')
            self.overall_changes += total_changes
            self.overall_alternatives_count += total_ambiguous

        print('')
        print(f'overall changes: +{self.overall_changes}')
        print(f'overall ambiguous strings: {self.overall_alternatives_count}')
        print('')

    def _save(self):
        print('saving files...')

        for target in self.target.files.values():
            with open(str(self.output / target.path.name), 'w') as f:
                f.write(str(target.parsed))

    def _report(self):
        print('')

        if self.overall_alternatives:
            print(textwrap.dedent('''\
                AMBIGUOUS STRINGS
                -----------------

                There were ambiguous strings. All alternatives are included as
                XML comments in the saved translations files. These comments will
                be lost when reformatting with lupdate.

                It is advisable to manually go through the list printed below
                and ensure all strings are used correctly. This often does not
                require specific language skills.

                '''))

            print(f'{self.overall_alternatives_count} AMBIGUOUS STRINGS IN {len(self.overall_alternatives.items())} FILES:')

            for group in sorted(self.overall_alternatives.keys()):
                all_alts = self.overall_alternatives[group]

                if not all_alts:
                    continue

                print(f'- {group.path.name} ({len(all_alts.items())} strings):')

                for key in sorted(all_alts.keys()):
                    alts = all_alts[key]
                    print(f'    - {key}')

                    for a in sorted(alts):
                        print(f'        - {a}')

            print('')

        if self.no_match:
            print(textwrap.dedent('''\
                UNMATCHED FILES
                ---------------

                There were files without any matching translations files. This
                might be a chance to contribute translations back.

                '''))

            print('NO MATCHING FILES FOR:')

            for i in self.no_match:
                print(f'- {i.language}: {i.path}')

            print('')

        if self.not_handled:
            print(textwrap.dedent('''\
                UNHANDLED FILES
                ---------------

                There were files that could not be handled at all. Make sure they
                are properly formatted and contain a language definition.

                "Source" translation files, i.e. base catalogues containing only
                untranslated strings, should not define a language and should
                be listed here. This does not require further action.

                '''))

            print('UNHANDLED FILES:')

            for i in self.not_handled:
                print(f'- {i.language}: {i.path}')

            print('')

        print(textwrap.dedent('''\
            CONCLUSION
            ----------

            Note: run lupdate on the updated files to make sure they are
                  formatted correctly. The binary may be called lupdate-qt5.

            Examples:
                  lupdate-qt5 qml src -ts translations/*.ts
                  lupdate-qt5 Opal -ts translations/*.ts
            '''))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent('''\
            Automatically merge Qt translations files using a same-text heuristic.

            Source and target file pairs will be matched based on the languages
            defined in the files. File names will parsed be used if no language
            is defined.

            Existing translations will not be overwritten.
            Use the -A flag to exclude alternative translations found in source
            files. By default, alternative translations will be added ...
        '''),
        epilog=textwrap.dedent('''\
            TODO:
            - support merging two or more files instead of directories
            - merge into Opal and replace opal-merge-translations.sh
            n mark all strings changed by this tool as 'unfinished'
            x support merging directories
            x count and report changes
            x report ambiguous strings requiring extra attention
        ''')
    )

    parser.add_argument('source', type=str, nargs='+',
                        help='one or more directories containing '
                             'translations files (.ts) to take translations from')
    parser.add_argument('target', type=str, nargs=1,
                        help='directory containing translations files (.ts) to '
                             'merge new translations into')

    parser.add_argument('--force', '-f', action='store_true', default=False,
                        help='overwrite existing files')
    parser.add_argument('--output', '-o', type=str, nargs='?',
                        help='optional output directory; files will be modified '
                             'in-place if no output directory is specified')

    args = parser.parse_args()

    Merger.run(args)

    sys.exit(0)
