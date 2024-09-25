#!/usr/bin/env python3
#
# This file is part of Opal.
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2022-2023 Mirian Margiani
#
# @@@ FILE VERSION 0.1.0
#

import argparse
from collections import defaultdict
import sys
import textwrap
import glob
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple

try:
    from bs4 import BeautifulSoup
    from bs4 import Comment as XmlComment
except ModuleNotFoundError as e:
    if __name__ == '__main__':
        pass  # handled after parsing cli arguments
    else:
        raise e


@dataclass
class Language:
    lang: str
    area: str

    @property
    def is_empty(self) -> bool:
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

    def __lt__(self, other) -> bool:  # required for sorting
        return str(self) < str(other)

    def __str__(self) -> str:
        if self.area:
            return f'{self.lang.lower()}_{self.area.upper()}'
        else:
            return self.lang.lower()

    def __hash__(self) -> int:
        return hash(str(self))


@dataclass
class TranslatedString:
    source: str
    translation: 'BeautifulSoup'
    context: str
    comment: str

    def __eq__(self, other) -> bool:
        return self.source == other.source \
            and self.context == other.context \
            and self.comment == other.comment


@dataclass
class TsFile:
    path: Path
    parsed: 'BeautifulSoup'
    strings: Dict[str, 'BeautifulSoup']
    language: Language

    class LanguageMissingError(Exception):
        pass

    @staticmethod
    def from_disk(path, require_language=True) -> 'TsFile':
        if not Path(path).is_file():
            raise FileNotFoundError(path)

        with open(path, 'r') as f:
            parsed = BeautifulSoup(f.read(), 'xml')

        strings = {}
        for elem in parsed.select('context > message'):
            string = elem.source.string

            if string is None or str(string) == '':
                continue

            strings[elem.source.string] = elem.translation

        language = None
        if elem := parsed.find('TS', recursive=False):
            if lang_str := getattr(elem, 'language', ''):
                language = Language.from_str(lang_str)

        if not language:
            if match := re.match(r'^.*?-(?P<lang_str>([a-z]{2})([-_][A-Z]{2})?)\.[tT][sS]$', str(path)):
                language = Language.from_str(match.group('lang_str'))

        if not language:
            if require_language:
                msg = f'cannot extract language from file "{path}"'
                raise TsFile.LanguageMissingError(msg)
            else:
                language = Language('', '')

        return TsFile(Path(path), parsed, strings, language)

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
    def from_disk(path, allow_single_file=False) -> 'TsDirectory':
        path = Path(path)

        if not path.exists():
            msg = f'file or directory "{path}" not found'
            raise FileNotFoundError(msg)
        elif allow_single_file and path.is_file():
            try:
                return TsDirectory.from_single_file(path, require_language=True)
            except TsFile.LanguageMissingError:
                print(f'warning: skipped file "{path}"')
                return TsDirectory(path, {})
        elif not path.is_dir():
            msg = f'directory "{path}" not found'
            raise FileNotFoundError(msg)

        files = {}
        for i in glob.iglob(str(path / '*.[tT][sS]'), recursive=False):
            try:
                ts = TsFile.from_disk(i)
            except TsFile.LanguageMissingError:
                print(f'warning: skipped file "{i}"')
                continue

            if ts.language in files:
                msg = f'language "{ts.language}" is already registered: {ts.path}, {files[ts.language].path}'
                raise TsDirectory.DuplicateLanguageError(msg)

            files[ts.language] = ts

        return TsDirectory(path, files)

    @staticmethod
    def from_single_file(path, require_language=True) -> 'TsDirectory':
        path = Path(path)

        if not path.exists():
            msg = f'file "{path}" not found'
            raise FileNotFoundError(msg)
        elif not path.is_file():
            if path.is_dir():
                msg = f'path "{path}" is not a file - use "from_disk()" to load a directory from disk'
            else:
                msg = f'path "{path}" must be a file'
            raise FileNotFoundError(msg)

        try:
            ts = TsFile.from_disk(path, require_language=require_language)
        except TsFile.LanguageMissingError:
            if require_language:
                msg = f'file "{path}" used as single-file directory does not specify a language'
                raise TsFile.LanguageMissingError(msg)
            else:
                pass

        return TsDirectory(path.parent, {ts.language: ts})


class Merger:
    def __init__(self, args):
        self.args = args

        self.sources: List[TsDirectory] = []
        self.target: TsDirectory = None

        self.pairs: Dict[TsFile, List[TsFile]] = defaultdict(list)
        self.handled_files: List[TsFile] = []
        self.no_match: List[TsFile] = []
        self.not_handled: List[TsFile] = []
        self.new_catalogues: Dict[Language, TsFile] = {}

        self.overall_changes: int = 0
        self.overall_alternatives: Dict[TsFile, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        self.overall_alternatives_count: int = 0
        self.overall_new_catalogues: int = 0

        self.output = args.output
        self.force = args.force

        self.base_catalogue = args.base_catalogue

        if self.output and Path(self.output).exists() and not self.force:
            msg = f'output directory exists: "{self.output}" (use -f to overwrite)'
            raise FileExistsError(msg)
        elif not self.output and not self.force:
            msg = 'no output directory defined, use -f to overwrite original target files ' + \
                  f'(merging into "{args.target[0]}")'
            raise FileExistsError(msg)
        elif self.output:
            self.output = Path(self.output)
            self.output.mkdir(parents=True, exist_ok=True)
        else:
            self.output = Path(args.target[0])

            if self.output.is_file():
                self.output = self.output.parent

            if not self.output.exists():
                msg = f'output directory "{self.output}" not found'
                raise FileNotFoundError(msg)
            elif not self.output.is_dir():
                msg = f'output path "{self.output}" must be a directory'
                raise FileNotFoundError(msg)

        if args.auto_base_catalogue:
            self.base_catalogue = self._detect_base_catalogue(args)

        if self.base_catalogue:
            self.base_catalogue = Path(self.base_catalogue)

            if not self.base_catalogue.exists():
                msg = f'base catalogue file "{self.base_catalogue}" not found'
                raise FileNotFoundError(msg)
            elif not self.base_catalogue.is_file():
                msg = f'base catalogue path "{self.base_catalogue}" must be a file'
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

    def _detect_base_catalogue(self, args) -> Path:
        print("detecting base catalogue...")
        target = Path(args.target[0])
        first = None
        second = None

        expected = None
        expected_dir = None

        if target.is_dir():
            print("- target is a directory")
            expected_dir = target

            for i in glob.iglob(str(target / '*.ts'), recursive=False):
                if not first:
                    first = Path(i).name
                elif not second:
                    second = Path(i).name
                else:
                    break
            print("- probing files:", first, second)
        elif target.is_file():
            print("- target is a file")
            first = target.name
            expected_dir = target.parent
            print(f"- probing '{first}' in '{expected_dir}'")

        if first and second:
            common = ''
            for x, y in zip(list(first), list(second)):
                if x == y:
                    common += x
                else:
                    break

            first = first[len(common):]
            print(f"- detected common base '{common}' with remainder '{first}'")

            if match := re.match(r'^-?([a-z]{2}(_[A-Z]{2})?)?\.[tT][sS]$', first):
                if common.endswith('-'):
                    common = common[:-1]

                expected = common + '.ts'
                print(f"- expecting '{expected}' from two files")
            else:
                expected = None  # fail
        elif first:
            if match := re.match(r'^(?P<base>.+?)(-[a-z]{2}(_[A-Z]{2})?)?\.[tT][sS]$', first):
                expected = match.group('base') + '.ts'
                print(f"- expecting '{expected}' from single file")
            else:
                expected = None  # fail
        else:
            expected = None  # fail

        if expected:
            expected = Path(expected_dir / expected)
            print(f"- file exists at {expected}")

            if expected.is_file():
                pass  # "return expected" below
            else:
                msg = f'expected to find the base catalogue at "{expected}" but ' + \
                      'that file does not exist, use -b to set the path manually'
                raise FileNotFoundError(msg)
        else:
            msg = 'failed to auto-detect the base catalogue, use -b to set the path manually'
            raise FileNotFoundError(msg)

        return expected

    def _collect(self):
        print('collecting files...')

        try:
            self.target = TsDirectory.from_disk(args.target[0], allow_single_file=True)
        except FileNotFoundError:
            msg = f'target directory not found at "{args.target[0]}"'
            raise FileNotFoundError(msg)
        except TsDirectory.DuplicateLanguageError:
            msg = f'target directory "{args.target[0]}" contains multiple files for the same language'
            print(f"warning: {msg}")

        try:
            for i in args.source:
                self.sources.append(TsDirectory.from_disk(i, allow_single_file=True))
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

        extra_catalogues: Dict[Language, TsFile] = {}

        for s in self.sources:
            for s_file in s.files.values():
                if s_file not in self.handled_files:
                    if not s_file.language.is_empty and self.base_catalogue:
                        if s_file.language not in extra_catalogues:
                            new_file = TsFile.from_disk(self.base_catalogue, require_language=False)
                            new_file.language = s_file.language
                            new_file.path = self.output / re.sub(r'\.[tT][sS]$', f'-{s_file.language}.ts', self.base_catalogue.name)
                            new_file.parsed.TS['language'] = str(s_file.language)
                            extra_catalogues[s_file.language] = new_file

                        self.pairs[extra_catalogues[s_file.language]].append(s_file)
                    else:
                        self.not_handled.append(s_file)

        if extra_catalogues:
            print(f'\n{len(extra_catalogues.keys())} new language catalogues:')
            self.new_catalogues = extra_catalogues
            self.overall_new_catalogues = len(self.new_catalogues.keys())

            for key in sorted(extra_catalogues.keys()):
                val = extra_catalogues[key]
                print(f'- {key}: {val.path}')

                for i in self.pairs[val]:
                    print(f'    - {i.path} ({i.language})')

    def _do_merge_pair(self, source: TsFile, target: TsFile) -> Tuple[int, Dict[str, List[str]]]:
        changes = 0
        alternatives = defaultdict(list)

        for key, own in target.strings.items():
            if key in source.strings:
                other = source.strings[key]
                has_numerus = False

                if len(other.select('numerusform')) != len(own.select('numerusform')):
                    has_numerus = True
                    own_nums = own.select('numerusform')

                    if not own.string and len(own_nums) == 1 and not own_nums[0].string:  # and getattr(own, 'type', '') == 'unfinished':
                        own.replace_with(other)  # insert all plural forms
                    else:
                        print("WARNING: string has numerusform in one file but not in other")
                        print(f"        {key}: {len(other.select('numerusform'))} vs. {len(own.select('numerusform'))}")
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
                        # del own['type']

                    if has_empty:
                        own['type'] = 'unfinished'
                elif other.string and not own.string:
                    own.string = other.string
                    changes += 1

                    if getattr(other, 'type', '') == 'unfinished':
                        own['type'] = 'unfinished'
                    else:
                        own['type'] = ''
                        # del own['type']
                elif other.string == own.string:
                    if getattr(other, 'type', '') != 'unfinished' or getattr(own, 'type', '') != 'unfinished':
                        own['type'] = ''
                        # del own['type']
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

        for target in self.pairs.keys():
            with open(str(self.output / target.path.name), 'w') as f:
                f.write(str(target.parsed))

    def _report(self):
        print('')

        summary = []

        if self.overall_changes:
            summary.append(f'{self.overall_changes} strings updated')
        else:
            summary.append('no strings updated')

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

            summary.append(f'{self.overall_alternatives_count} ambiguous strings in {len(self.overall_alternatives.items())} files')
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

                There were target files without any matching source translations
                files. This might be a chance to contribute translations back.

                '''))

            summary.append(f'{len(self.no_match)} unmatched files')
            print('NO MATCHING FILES FOR:')

            for i in self.no_match:
                print(f'- {i.language}: {i.path}')

            print('')

        if self.not_handled:
            print(textwrap.dedent('''\
                UNHANDLED FILES
                ---------------

                There were source files without any matching target translations
                files. This means their languages are not yet supported by the
                target. Use the '-b' option to enable creating new catalogues.

                Some files may also be improperly formatted or do not contain
                a language definition. These files cannot be handled.

                "Source" translation files, i.e. base catalogues containing only
                untranslated strings, should not define a language and should
                also be listed here. This does not require further action.

                '''))

            summary.append(f'{len(self.not_handled)} unhandled files')
            print('UNHANDLED FILES:')

            for i in self.not_handled:
                print(f'- {i.language}: {i.path}')

            print('')

        if self.new_catalogues:
            print(textwrap.dedent('''\
                NEW TRANSLATIONS CATALOGUES
                ---------------------------

                New translations catalogues have been created in the target for
                languages that existed only in the sources.

                Note: run 'lupdate' and then run this migration again to make
                      sure all plural forms are properly created in the target

                '''))

            summary.append(f'{len(self.new_catalogues.keys())} new catalogues')
            print('NEW LANGUAGES:')

            for lang in sorted(self.new_catalogues.keys()):
                print(f'- {lang}: {self.new_catalogues[lang].path}')

            print('')

        print(textwrap.dedent('''\
            CONCLUSION
            ----------
            '''))

        for i in summary:
            print(f'- {i}')

        print(textwrap.dedent('''\

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
            defined in the files. File names will parsed if no language is defined.

            Already translated strings will not be overwritten.
        '''),
        epilog=textwrap.dedent('''\
            Examples:
                # Merge translations from one directory into another, creating
                # missing catalogues on the way.

                merge-translations.py libs/opal-translations/* translations -B -o new-translations


                # Same as above, but overwrite original translation files instead
                # of creating a new directory. Use with care.

                merge-translations.py libs/opal-translations/* translations -B -f


                # Same as above but save all output to a file and run 'lupdate' afterwards.

                merge-translations.py libs/opal-translations/* translations -B -f | tee merge.log && lupdate-qt5 qml src -ts translations/*.ts


                # Merge two translations files into the second. Aborts if
                # languages don't match.

                merge-translations.py your-translations.ts my-translations.ts -f


                # Update a single translations catalogue from wherever possible
                # into a new file below out/.

                merge-translations.py all-translations my-translations.ts -o out
        ''')
    )

    parser.add_argument('source', type=str, nargs='+',
                        help='one or more files or directories containing '
                             'translations files (.ts) to take translations from')
    parser.add_argument('target', type=str, nargs=1,
                        help='file or directory containing translations files (.ts) to '
                             'merge new translations into')

    parser.add_argument('--force', '-f', action='store_true', default=False,
                        help='overwrite existing files (default: disabled)')
    parser.add_argument('--output', '-o', type=str, nargs='?',
                        help='optional output directory; files will be modified '
                             'in-place if no output directory is specified')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--base-catalogue', '-b', type=str, nargs='?',
                       help='use this translations file as the basis for creating '
                            'missing translations that do not yet exist on the target side. '
                            'If no base catalogue is specified, missing translations '
                            'will be ignored.')
    group.add_argument('--auto-base-catalogue', '-B', action='store_true', default=False,
                       help='try to automatically detect the base catalogue to use '
                            'for creating missing translations that do not yet exist '
                            'on the target side. (default: disabled)')

    args = parser.parse_args()

    try:
        from bs4 import BeautifulSoup
    except ModuleNotFoundError:
        print(textwrap.dedent('''\
            error: missing python module "BeautifulSoup"

            BeautifulSoup is required for reading and writing translations files.
            Please install it and try again. Note: the package is usually called
            beautifulsoup4 or bs4, e.g. python310-beautifulsoup4.
        '''))
        sys.exit(1)

    Merger.run(args)

    sys.exit(0)
