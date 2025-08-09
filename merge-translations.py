#!/usr/bin/env python3
#
# This file is part of Opal.
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2022-2025 Mirian Margiani
#
# @@@ FILE VERSION 0.1.0
#

import argparse
from collections import defaultdict
import sys
import textwrap
import glob
import re
import string
import copy
from os.path import commonprefix
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple

TranslationAlternatives = Dict['TranslatedString', List['TranslatedString']]

try:
    from bs4 import BeautifulSoup
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

        if self.area and not other.area:
            return True
        elif not self.area and other.area:
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
    parsed: 'BeautifulSoup'
    source: str
    translation: 'BeautifulSoup.Tag'
    context: str
    comment: str
    origin: str

    def set_comment(self, value: str) -> None:
        if node := self.translation.parent.select_one('comment'):
            node.string = value
        else:
            new_tag = self.parsed.new_tag('comment')
            new_tag.string = value
            self.translation.parent.append(new_tag)

    def compatibility(self, other: 'TranslatedString') -> int:
        priority = 0

        if self.source != other.source:
            return 0

        if self.context == other.context:
            priority += 4
        # WARNING Opal-specific special cases
        elif 'Opal.About.Common' in [self.context, other.context] and \
                'AboutPage' in [self.context, other.context]:
            priority += 4

        if self.comment == other.comment:
            priority += 2
        elif (not self.comment and other.comment) or \
                (self.comment and not other.comment):
            # If one string was updated and the other was not, then it is possible
            # to get different comments.
            priority += 1
        else:
            # if the disambiguation is different then the strings are
            # clearly not compatible
            return 0

        if self.has_plurals == other.has_plurals:
            priority += 1

            if len(self.translation.select('numerusform')) == \
                    len(other.translation.select('numerusform')):
                # The number of plural forms may differ. This is not necessarily
                # an indication that the strings are incompatible.
                priority += 1
        else:
            # Support for plurals requires code changes which is a conscious
            # decision by the developer. If one string has plural forms and the
            # other does not, then the strings are incompatible.
            return 0

        return priority

    @property
    def is_finished(self) -> bool:
        if 'type' in self.translation.attrs:
            return self.translation['type'] != 'unfinished'
        return True

    @is_finished.setter
    def is_finished(self, value: bool) -> None:
        if value:
            self.translation['type'] = ''
        else:
            self.translation['type'] = 'unfinished'

    @property
    def has_content(self) -> bool:
        return self.translation.get_text(strip=True) != ''

    @property
    def has_plurals(self) -> bool:
        if 'numerus' in self.translation.parent.attrs:
            return self.translation.parent['numerus'] == 'yes'
        return False

    @has_plurals.setter
    def has_plurals(self, value: bool):
        if value:
            self.translation.parent['numerus'] = 'yes'
        else:
            self.translation.parent['numerus'] = ''

    def __eq__(self, other) -> bool:
        if self.source != other.source \
                or self.has_plurals != other.has_plurals \
                or self.is_finished != other.is_finished \
                or self.comment != other.comment:
            return False

        if self.has_plurals:
            own_nums = self.translation.select('numerusform')
            other_nums = other.translation.select('numerusform')

            if len(own_nums) != len(other_nums):
                return False

            if [x.string for x in own_nums] != [x.string for x in other_nums]:
                return False
        else:
            if self.translation.string != other.translation.string:
                return False

        # NOTE self.context is deliberately not included in the comparison
        #      to allow checking whether actual contents are different

        return True

    def __hash__(self) -> int:
        return hash(str(self.origin) + str(self.translation))


@dataclass
class TsFile:
    path: Path
    parsed: 'BeautifulSoup'
    strings: Dict[str, List[TranslatedString]]
    language: Language

    class LanguageMissingError(Exception):
        pass

    @staticmethod
    def from_disk(path, require_language=True) -> 'TsFile':
        if not Path(path).is_file():
            raise FileNotFoundError(path)

        with open(path, 'r') as f:
            parsed = BeautifulSoup(f.read(), 'xml', preserve_whitespace_tags=[
                'comment', 'translation', 'source', 'numerusform'
            ])

        strings = defaultdict(list)
        for elem in parsed.select('context > message'):
            string = elem.source.string

            if string is None or str(string) == '':
                continue

            if comment := elem.select_one('comment'):
                comment = comment.get_text(strip=True)
            else:
                comment = ''

            origin = Path(path).absolute()
            strings[elem.source.string] += [
                TranslatedString(parsed,
                                 elem.source.string,
                                 elem.translation,
                                 elem.parent.select_one('context > name').string,
                                 comment,
                                 origin)
            ]

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
    def from_disk(path, allow_single_file=False, ignore=[]) -> 'TsDirectory':
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
            if Path(i).absolute() in ignore:
                continue

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
        self.overwrite: bool = args.overwrite

        self.pairs: Dict[TsFile, List[TsFile]] = defaultdict(list)
        self.handled_files: List[TsFile] = []
        self.no_match: List[TsFile] = []
        self.not_handled: List[TsFile] = []
        self.new_catalogues: Dict[Language, TsFile] = {}

        self.overall_changes: int = 0
        self.overall_alternatives: Dict[TsFile, Dict[str, Dict[str, list]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        self.overall_alternatives_count: int = 0
        self.overall_new_catalogues: int = 0

        self.output = args.output
        self.force = args.force

        self.base_catalogue = args.base_catalogue

        self.interactive = args.interactive
        self.always_preferred: List[TsFile] = []

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

        expected = None
        expected_dir = None

        if target.is_dir():
            print("- target is a directory")
            expected_dir = target
            names = [Path(x).name for x in glob.iglob(str(target / '*.ts'), recursive=False)]
            expected = commonprefix(names)
        elif target.is_file():
            print("- target is a file")
            expected_dir = target.parent

            if match := re.match(r'^(?P<base>.+?)(-[a-z]{2}(_[A-Z]{2})?)?\.[tT][sS]$', target.name):
                expected = match.group('base')
            else:
                expected = None  # fail
        else:
            expected = None  # fail

        if expected:
            print(f"- looking for base catalogue '{expected}.ts'")
            found = (list(glob.iglob(str(Path(expected_dir / expected).absolute()) + '.[tT][sS]')) or [''])[0]

            if found and Path(found).is_file():
                expected = Path(found)
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
            self.target = TsDirectory.from_disk(args.target[0], allow_single_file=True, ignore=[self.base_catalogue])
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

    def _do_merge_string(self,
                         key: str,
                         source_options: list[TranslatedString],
                         target_options: list[TranslatedString],
                         overwrite: bool | None = None
                         ) -> tuple[str, int, TranslationAlternatives]:
        changes = 0
        alternatives = defaultdict(list)

        for target in target_options:
            matching_source = [[x, target.compatibility(x)] for x in source_options if target.compatibility(x) > 0]
            matching_source.sort(key=lambda x: x[1])

            if not matching_source:
                continue
            else:
                matching_source: TranslatedString = matching_source[0][0]  # take the highest priority match

            if not matching_source.has_content:
                continue

            if target == matching_source:
                continue

            if target.is_finished and target.has_content and \
                    ((overwrite is None and not self.overwrite)
                     or (overwrite is not None and overwrite is False)):
                if matching_source not in alternatives[target]:
                    alternatives[target].append(matching_source)
                continue

            if matching_source.comment and not target.comment:
                target.set_comment(matching_source.comment)

            if matching_source.has_plurals:
                for i in target.translation.select('numerusform'):
                    i.extract()

                for i in matching_source.translation.select('numerusform'):
                    target.translation.append(copy.copy(i))

                target.has_plurals = True
            else:
                target.translation.string = str(matching_source.translation.string)

            target.is_finished = matching_source.is_finished

            changes += 1

        return (key, changes, alternatives)

    def _do_merge_pair(self, source: TsFile, target: TsFile) -> Tuple[int, Dict[str, TranslationAlternatives]]:
        changes = 0
        alternatives = defaultdict(dict)

        for key, into_details in target.strings.items():
            if key not in source.strings:
                continue

            outof_details = source.strings[key]

            _key, _changes, _alternatives = self._do_merge_string(key, outof_details, into_details)
            changes += _changes

            if _alternatives:
                alternatives[key] = _alternatives

        return (changes, alternatives)

    def _report_alternatives(self, key: TsFile) -> None:
        all_alts = self.overall_alternatives[key]

        if not all_alts:
            return

        target = Path(key.path).absolute()
        print(f'- {len(all_alts.items())} ambiguous strings in {key.path.name}:')

        for key in sorted(all_alts.keys()):
            alts = all_alts[key]
            print(f'    - {key}')

            if len(alts.keys()) > 1:
                print("error: more than one source string is not supported")
                continue

            current = list(alts.keys())[0]
            current_text = current.translation.get_text()
            origins = defaultdict(list)
            origins[current_text] = []

            for a in alts[current]:
                path = str(Path(a.origin).relative_to(target, walk_up=True))
                origins[a.translation.get_text()].append(path)

            origins = {k: sorted(set(v)) for k, v in origins.items()}

            print(f'        - {current_text.strip()}    [{", ".join(['current'] + origins[current_text])}]')

            for a in alts[current]:
                text = a.translation.get_text()
                print(f'        - {text.strip()}    [{", ".join(origins[text])}]')

        print('')

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
                    for k, v in alts.items():
                        self.overall_alternatives[target][key][k] += v

                print(f'- {source.path} ({source.language}) [+{changes} / {alternatives_count}]')

            print(f'- total changes: +{total_changes} / {total_ambiguous}')
            self.overall_changes += total_changes
            self.overall_alternatives_count += total_ambiguous

        print('')
        print(f'overall changes: +{self.overall_changes}')
        print(f'overall ambiguous strings: {self.overall_alternatives_count}')
        print('')

        if self.interactive:
            for group in sorted(self.overall_alternatives.keys()):
                all_alts = self.overall_alternatives[group]

                if not all_alts:
                    continue

                title = f' merging {group.path} '
                print(f'\n{title:-^80}\n')

                target = Path(group.path).absolute()
                self._report_alternatives(group)

                prefer_all = None

                for key in sorted(all_alts.keys()):
                    alts = all_alts[key]
                    print(f'\n{"STRING:":<25s} {key}')

                    if len(alts.keys()) > 1:
                        print("error: more than one source string is not supported")
                        continue

                    current = list(alts.keys())[0]
                    options = {}
                    origin_options = {}
                    status_ = {}
                    by_origin = {}

                    alphabet = list(string.ascii_lowercase)
                    index = 0
                    origin_index = 0
                    options[f'[{index + 1}] {current.translation.get_text().strip()}  [current]'] = current
                    status_[f'{current.translation.get_text().strip()}  [current]'] = current
                    current_origin_option = {f'[{alphabet[origin_index]}] always prefer current': target}
                    status_ = status_ | {'always prefer current': target}
                    index += 1
                    origin_index += 1

                    by_origin[target] = current

                    for a in alts[current]:
                        options[f'[{index + 1}] {a.translation.get_text().strip()}  [{str(a.origin.relative_to(target, walk_up=True))}]'] = a
                        status_[f'{a.translation.get_text().strip()}  [{str(a.origin.relative_to(target, walk_up=True))}]'] = a
                        index += 1

                        by_origin[a.origin] = a
                        origin_key = f'always prefer {a.origin.relative_to(target, walk_up=True)}'

                        for i in alts[current]:
                            if origin_key not in status_:
                                origin_options[f'[{alphabet[origin_index]}] {origin_key}'] = i
                                origin_index += 1

                            status_ = status_ | {origin_key: a.origin for a in alts[current]}

                    options = options | {None: None} | current_origin_option | origin_options
                    options_keys = list(options.keys())

                    def status_bar(key):
                        selected = status_[key]

                        if isinstance(selected, Path):
                            return f'All further ambiguous strings for {group.path.name} will be taken from {selected}.'
                        else:
                            extra = []

                            if selected.comment:
                                extra.append(selected.comment)

                            if selected.has_plurals:
                                extra.append('with PLURAL forms')
                            else:
                                extra.append('in SINGULAR form')

                            if selected.is_finished:
                                extra.append('marked as FINISHED')
                            else:
                                extra.append('marked as INCOMPLETE')

                            return f'[{selected.context}] ' + ' | '.join(extra)

                    def replace_translation(new_translation):
                        print(f'{"CURRENT TRANSLATION:":<25s} {current.translation.get_text().strip()}')
                        print(f'{"NEW TRANSLATION:":<25s} {new_translation.translation.get_text().strip()}')
                        print()
                        del self.overall_alternatives[group][key]
                        self._do_merge_string(key, [new_translation], [current], overwrite=True)

                    if prefer_all and prefer_all in by_origin:
                        replace_translation(by_origin[prefer_all])
                        continue

                    title = f'\nAlternatives for: {key}'
                    terminal_menu = TerminalMenu(options_keys,
                                                 title=title,
                                                 status_bar=status_bar)
                    menu_entry_index = terminal_menu.show()

                    if menu_entry_index is None:
                        print("aborting...")
                        sys.exit(0)  # user aborted with ctrl-c

                    selected = options[options_keys[menu_entry_index]]

                    if isinstance(selected, Path):
                        print(f'\nAll further ambiguous strings for {group.path.name} will be taken from:\n{selected}')
                        prefer_all = selected
                        replace_translation(by_origin[selected])
                    else:
                        replace_translation(selected)

            # all alternatives for this file are now resolved
            del self.overall_alternatives[group]

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

                There were ambiguous strings.

                It is advisable to manually go through the list printed below
                and ensure all strings are used correctly. This often does not
                require specific language skills.

                '''))

            summary.append(f'{self.overall_alternatives_count} ambiguous strings in {len(self.overall_alternatives.items())} files')
            print(f'{self.overall_alternatives_count} AMBIGUOUS STRINGS IN {len(self.overall_alternatives.items())} FILES:')

            for group in sorted(self.overall_alternatives.keys()):
                self._report_alternatives(group)

            print()

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



#
# SIMPLE TERM MENU
#

import copy
import ctypes
import io
import locale
import os
import platform
import re
import shlex
import signal
import string
import subprocess
import sys
from locale import getlocale
from types import FrameType
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Match,
    Optional,
    Pattern,
    Sequence,
    Set,
    TextIO,
    Tuple,
    Union,
    cast,
)

try:
    import termios
except ImportError as e:
    raise NotImplementedError('"{}" is currently not supported.'.format(platform.system())) from e


# __author__ = "Ingo Meyer"
# __email__ = "i.meyer@fz-juelich.de"
# __copyright__ = "Copyright © 2021 Forschungszentrum Jülich GmbH. All rights reserved."
# __license__ = "MIT"
# __version_info__ = (1, 6, 6)
# __version__ = ".".join(map(str, __version_info__))


DEFAULT_ACCEPT_KEYS = ("enter",)
DEFAULT_CLEAR_MENU_ON_EXIT = True
DEFAULT_CLEAR_SCREEN = False
DEFAULT_CYCLE_CURSOR = True
DEFAULT_EXIT_ON_SHORTCUT = True
DEFAULT_MENU_CURSOR = "> "
DEFAULT_MENU_CURSOR_STYLE = ("fg_red", "bold")
DEFAULT_MENU_HIGHLIGHT_STYLE = ("standout",)
DEFAULT_MULTI_SELECT = False
DEFAULT_MULTI_SELECT_CURSOR = "[*] "
DEFAULT_MULTI_SELECT_CURSOR_BRACKETS_STYLE = ("fg_gray",)
DEFAULT_MULTI_SELECT_CURSOR_STYLE = ("fg_yellow", "bold")
DEFAULT_MULTI_SELECT_KEYS = (" ", "tab")
DEFAULT_MULTI_SELECT_SELECT_ON_ACCEPT = True
DEFAULT_PREVIEW_BORDER = True
DEFAULT_PREVIEW_SIZE = 0.25
DEFAULT_PREVIEW_TITLE = "preview"
DEFAULT_QUIT_KEYS = ("escape", "q", "ctrl-g")
DEFAULT_SEARCH_CASE_SENSITIVE = False
DEFAULT_SEARCH_HIGHLIGHT_STYLE = ("fg_black", "bg_yellow", "bold")
DEFAULT_SEARCH_KEY = "/"
DEFAULT_SHORTCUT_BRACKETS_HIGHLIGHT_STYLE = ("fg_gray",)
DEFAULT_SHORTCUT_KEY_HIGHLIGHT_STYLE = ("fg_blue",)
DEFAULT_SHOW_MULTI_SELECT_HINT = False
DEFAULT_SHOW_SEARCH_HINT = False
DEFAULT_SHOW_SHORTCUT_HINTS = False
DEFAULT_SHOW_SHORTCUT_HINTS_IN_STATUS_BAR = True
DEFAULT_STATUS_BAR_BELOW_PREVIEW = False
DEFAULT_STATUS_BAR_STYLE = ("fg_yellow", "bg_black")
MIN_VISIBLE_MENU_ENTRIES_COUNT = 3


class InvalidParameterCombinationError(Exception):
    pass


class InvalidStyleError(Exception):
    pass


class NoMenuEntriesError(Exception):
    pass


class PreviewCommandFailedError(Exception):
    pass


class UnknownMenuEntryError(Exception):
    pass


def get_locale() -> str:
    user_locale = locale.getlocale()[1]
    if user_locale is None:
        return "ascii"
    else:
        return user_locale.lower()


def wcswidth(text: str) -> int:
    if not hasattr(wcswidth, "libc"):
        try:
            if platform.system() == "Darwin":
                wcswidth.libc = ctypes.cdll.LoadLibrary("libSystem.dylib")  # type: ignore
            else:
                wcswidth.libc = ctypes.cdll.LoadLibrary("libc.so.6")  # type: ignore
        except OSError:
            wcswidth.libc = None  # type: ignore
    if wcswidth.libc is not None:  # type: ignore
        try:
            user_locale = get_locale()
            # First replace any null characters with the unicode replacement character (U+FFFD) since they cannot be
            # passed in a `c_wchar_p`
            encoded_text = text.replace("\0", "\uFFFD").encode(encoding=user_locale, errors="replace")
            return wcswidth.libc.wcswidth(  # type: ignore
                ctypes.c_wchar_p(encoded_text.decode(encoding=user_locale)), len(encoded_text)
            )
        except AttributeError:
            pass
    return len(text)


def static_variables(**variables: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        for key, value in variables.items():
            setattr(f, key, value)
        return f

    return decorator


class BoxDrawingCharacters:
    if getlocale()[1] == "UTF-8":
        # Unicode box characters
        horizontal = "─"
        vertical = "│"
        upper_left = "┌"
        upper_right = "┐"
        lower_left = "└"
        lower_right = "┘"
    else:
        # ASCII box characters
        horizontal = "-"
        vertical = "|"
        upper_left = "+"
        upper_right = "+"
        lower_left = "+"
        lower_right = "+"


class TerminalMenu:
    class Search:
        def __init__(
            self,
            menu_entries: Iterable[str],
            search_text: Optional[str] = None,
            case_senitive: bool = False,
            show_search_hint: bool = False,
        ):
            self._menu_entries = menu_entries
            self._case_sensitive = case_senitive
            self._show_search_hint = show_search_hint
            self._matches = []  # type: List[Tuple[int, Match[str]]]
            self._search_regex = None  # type: Optional[Pattern[str]]
            self._change_callback = None  # type: Optional[Callable[[], None]]
            # Use the property setter since it has some more logic
            self.search_text = search_text

        def _update_matches(self) -> None:
            if self._search_regex is None:
                self._matches = []
            else:
                matches = []
                for i, menu_entry in enumerate(self._menu_entries):
                    match_obj = self._search_regex.search(menu_entry)
                    if match_obj:
                        matches.append((i, match_obj))
                self._matches = matches

        @property
        def matches(self) -> List[Tuple[int, Match[str]]]:
            return list(self._matches)

        @property
        def search_regex(self) -> Optional[Pattern[str]]:
            return self._search_regex

        @property
        def search_text(self) -> Optional[str]:
            return self._search_text

        @search_text.setter
        def search_text(self, text: Optional[str]) -> None:
            self._search_text = text
            search_text = self._search_text
            self._search_regex = None
            while search_text and self._search_regex is None:
                try:
                    self._search_regex = re.compile(search_text, flags=re.IGNORECASE if not self._case_sensitive else 0)
                except re.error:
                    search_text = search_text[:-1]
            self._update_matches()
            if self._change_callback:
                self._change_callback()

        @property
        def change_callback(self) -> Optional[Callable[[], None]]:
            return self._change_callback

        @change_callback.setter
        def change_callback(self, callback: Optional[Callable[[], None]]) -> None:
            self._change_callback = callback

        @property
        def occupied_lines_count(self) -> int:
            if not self and not self._show_search_hint:
                return 0
            else:
                return 1

        def __bool__(self) -> bool:
            return self._search_text is not None

        def __contains__(self, menu_index: int) -> bool:
            return any(i == menu_index for i, _ in self._matches)

        def __len__(self) -> int:
            return wcswidth(self._search_text) if self._search_text is not None else 0

    class Selection:
        def __init__(self, preselected_indices: Optional[Iterable[int]] = None):
            self._selected_menu_indices = set(preselected_indices) if preselected_indices is not None else set()

        def clear(self) -> None:
            self._selected_menu_indices.clear()

        def add(self, menu_index: int) -> None:
            self[menu_index] = True

        def remove(self, menu_index: int) -> None:
            self[menu_index] = False

        def toggle(self, menu_index: int) -> bool:
            self[menu_index] = menu_index not in self._selected_menu_indices
            return self[menu_index]

        def __bool__(self) -> bool:
            return bool(self._selected_menu_indices)

        def __contains__(self, menu_index: int) -> bool:
            return menu_index in self._selected_menu_indices

        def __getitem__(self, menu_index: int) -> bool:
            return menu_index in self._selected_menu_indices

        def __setitem__(self, menu_index: int, is_selected: bool) -> None:
            if is_selected:
                self._selected_menu_indices.add(menu_index)
            else:
                self._selected_menu_indices.remove(menu_index)

        def __iter__(self) -> Iterator[int]:
            return iter(self._selected_menu_indices)

        @property
        def selected_menu_indices(self) -> Tuple[int, ...]:
            return tuple(sorted(self._selected_menu_indices))

    class View:
        def __init__(
            self,
            menu_entries: Iterable[str],
            search: "TerminalMenu.Search",
            selection: "TerminalMenu.Selection",
            viewport: "TerminalMenu.Viewport",
            cycle_cursor: bool = True,
            skip_indices: List[int] = [],
        ):
            self._menu_entries = list(menu_entries)
            self._search = search
            self._selection = selection
            self._viewport = viewport
            self._cycle_cursor = cycle_cursor
            self._active_displayed_index = None  # type: Optional[int]
            self._skip_indices = skip_indices
            self.update_view()

        def update_view(self) -> None:
            if self._search and self._search.search_text != "":
                self._displayed_index_to_menu_index = tuple(i for i, match_obj in self._search.matches)
            else:
                self._displayed_index_to_menu_index = tuple(range(len(self._menu_entries)))
            self._menu_index_to_displayed_index = {
                menu_index: displayed_index
                for displayed_index, menu_index in enumerate(self._displayed_index_to_menu_index)
            }
            self._active_displayed_index = 0 if self._displayed_index_to_menu_index else None
            self._viewport.num_displayed_menu_entries = len(self._displayed_index_to_menu_index)
            self._viewport.search_lines_count = self._search.occupied_lines_count
            self._viewport.keep_visible(self._active_displayed_index)

        def increment_active_index(self) -> None:
            if self._active_displayed_index is not None:
                if self._active_displayed_index + 1 < self._viewport.num_displayed_menu_entries:
                    self._active_displayed_index += 1
                elif self._cycle_cursor:
                    self._active_displayed_index = 0
                self._viewport.keep_visible(self._active_displayed_index)

                if self._displayed_index_to_menu_index[self._active_displayed_index] in self._skip_indices:
                    self.increment_active_index()

        def decrement_active_index(self) -> None:
            if self._active_displayed_index is not None:
                if self._active_displayed_index > 0:
                    self._active_displayed_index -= 1
                elif self._cycle_cursor:
                    self._active_displayed_index = self._viewport.num_displayed_menu_entries - 1
                self._viewport.keep_visible(self._active_displayed_index)

                if self._displayed_index_to_menu_index[self._active_displayed_index] in self._skip_indices:
                    self.decrement_active_index()

        def page_down(self) -> None:
            if self._active_displayed_index is None:
                return
            self._viewport.page_down()
            self._active_displayed_index = min(
                self._active_displayed_index + self._viewport.size, self._viewport.num_displayed_menu_entries - 1
            )

        def page_up(self) -> None:
            if self._active_displayed_index is None:
                return
            self._viewport.page_up()
            self._active_displayed_index = max(self._active_displayed_index - self._viewport.size, 0)

        def is_visible(self, menu_index: int) -> bool:
            return menu_index in self._menu_index_to_displayed_index and (
                self._viewport.lower_index
                <= self._menu_index_to_displayed_index[menu_index]
                <= self._viewport.upper_index
            )

        def convert_menu_index_to_displayed_index(self, menu_index: int) -> Optional[int]:
            if menu_index in self._menu_index_to_displayed_index:
                return self._menu_index_to_displayed_index[menu_index]
            else:
                return None

        def convert_displayed_index_to_menu_index(self, displayed_index: int) -> int:
            return self._displayed_index_to_menu_index[displayed_index]

        @property
        def active_menu_index(self) -> Optional[int]:
            if self._active_displayed_index is not None:
                return self._displayed_index_to_menu_index[self._active_displayed_index]
            else:
                return None

        @active_menu_index.setter
        def active_menu_index(self, value: int) -> None:
            self.active_displayed_index = self._menu_index_to_displayed_index[value]

        @property
        def active_displayed_index(self) -> Optional[int]:
            return self._active_displayed_index

        @active_displayed_index.setter
        def active_displayed_index(self, value: int) -> None:
            self._active_displayed_index = value
            self._viewport.keep_visible(self._active_displayed_index)

        @property
        def max_displayed_index(self) -> int:
            return self._viewport.num_displayed_menu_entries - 1

        @property
        def displayed_selected_indices(self) -> List[int]:
            return [
                self._menu_index_to_displayed_index[selected_index]
                for selected_index in self._selection
                if selected_index in self._menu_index_to_displayed_index
            ]

        def __bool__(self) -> bool:
            return self._active_displayed_index is not None

        def __iter__(self) -> Iterator[Tuple[int, int, str]]:
            for displayed_index, menu_index in enumerate(self._displayed_index_to_menu_index):
                if self._viewport.lower_index <= displayed_index <= self._viewport.upper_index:
                    yield (displayed_index, menu_index, self._menu_entries[menu_index])

    class Viewport:
        def __init__(
            self,
            num_displayed_menu_entries: int,
            title_lines_count: int,
            status_bar_lines_count: int,
            preview_lines_count: int,
            search_lines_count: int,
        ):
            self._num_displayed_menu_entries = num_displayed_menu_entries
            self._title_lines_count = title_lines_count
            self._status_bar_lines_count = status_bar_lines_count
            # Use the property setter since it has some more logic
            self.preview_lines_count = preview_lines_count
            self.search_lines_count = search_lines_count
            self._num_lines = self._calculate_num_lines()
            self._viewport = (0, min(self._num_displayed_menu_entries, self._num_lines) - 1)
            self.keep_visible(cursor_position=None, refresh_terminal_size=False)

        def _calculate_num_lines(self) -> int:
            return (
                TerminalMenu._num_lines()
                - self._title_lines_count
                - self._status_bar_lines_count
                - self._preview_lines_count
                - self._search_lines_count
            )

        def keep_visible(self, cursor_position: Optional[int], refresh_terminal_size: bool = True) -> None:
            # Treat `cursor_position=None` like `cursor_position=0`
            if cursor_position is None:
                cursor_position = 0
            if refresh_terminal_size:
                self.update_terminal_size()
            if self._viewport[0] <= cursor_position <= self._viewport[1]:
                # Cursor is already visible
                return
            if cursor_position < self._viewport[0]:
                scroll_num = cursor_position - self._viewport[0]
            else:
                scroll_num = cursor_position - self._viewport[1]
            self._viewport = (self._viewport[0] + scroll_num, self._viewport[1] + scroll_num)

        def page_down(self) -> None:
            self.scroll(self.size)

        def page_up(self) -> None:
            self.scroll(-self.size)

        def scroll(self, number_of_lines: int) -> None:
            if number_of_lines < 0:
                scroll_num = max(-self._viewport[0], number_of_lines)
            else:
                scroll_num = min(max(0, self._num_displayed_menu_entries - self._viewport[1] - 1), number_of_lines)
            self._viewport = (self._viewport[0] + scroll_num, self._viewport[1] + scroll_num)

        def update_terminal_size(self) -> None:
            num_lines = self._calculate_num_lines()
            if num_lines != self._num_lines:
                # First let the upper index grow or shrink
                upper_index = min(num_lines, self._num_displayed_menu_entries) - 1
                # Then, use as much space as possible for the `lower_index`
                lower_index = max(0, upper_index - num_lines)
                self._viewport = (lower_index, upper_index)
                self._num_lines = num_lines

        @property
        def lower_index(self) -> int:
            return self._viewport[0]

        @property
        def upper_index(self) -> int:
            return self._viewport[1]

        @property
        def viewport(self) -> Tuple[int, int]:
            return self._viewport

        @property
        def size(self) -> int:
            return self._viewport[1] - self._viewport[0] + 1

        @property
        def num_displayed_menu_entries(self) -> int:
            return self._num_displayed_menu_entries

        @num_displayed_menu_entries.setter
        def num_displayed_menu_entries(self, num_displayed_menu_entries: int) -> None:
            self._num_displayed_menu_entries = num_displayed_menu_entries

        @property
        def title_lines_count(self) -> int:
            return self._title_lines_count

        @property
        def status_bar_lines_count(self) -> int:
            return self._status_bar_lines_count

        @status_bar_lines_count.setter
        def status_bar_lines_count(self, value: int) -> None:
            self._status_bar_lines_count = value

        @property
        def preview_lines_count(self) -> int:
            return self._preview_lines_count

        @preview_lines_count.setter
        def preview_lines_count(self, value: int) -> None:
            self._preview_lines_count = min(
                value if value >= 3 else 0,
                TerminalMenu._num_lines()
                - self._title_lines_count
                - self._status_bar_lines_count
                - MIN_VISIBLE_MENU_ENTRIES_COUNT,
            )

        @property
        def search_lines_count(self) -> int:
            return self._search_lines_count

        @search_lines_count.setter
        def search_lines_count(self, value: int) -> None:
            self._search_lines_count = value

        @property
        def must_scroll(self) -> bool:
            return self._num_displayed_menu_entries > self._num_lines

    _codename_to_capname = {
        "bg_black": "setab 0",
        "bg_blue": "setab 4",
        "bg_cyan": "setab 6",
        "bg_gray": "setab 7",
        "bg_green": "setab 2",
        "bg_purple": "setab 5",
        "bg_red": "setab 1",
        "bg_yellow": "setab 3",
        "bold": "bold",
        "clear": "clear",
        "colors": "colors",
        "cursor_down": "cud1",
        "cursor_invisible": "civis",
        "cursor_left": "cub1",
        "cursor_right": "cuf1",
        "cursor_up": "cuu1",
        "cursor_visible": "cnorm",
        "delete_line": "dl1",
        "down": "kcud1",
        "end": "kend",
        "enter_application_mode": "smkx",
        "exit_application_mode": "rmkx",
        "fg_black": "setaf 0",
        "fg_blue": "setaf 4",
        "fg_cyan": "setaf 6",
        "fg_gray": "setaf 7",
        "fg_green": "setaf 2",
        "fg_purple": "setaf 5",
        "fg_red": "setaf 1",
        "fg_yellow": "setaf 3",
        "home": "khome",
        "italics": "sitm",
        "page_down": "knp",
        "page_up": "kpp",
        "reset_attributes": "sgr0",
        "standout": "smso",
        "underline": "smul",
        "up": "kcuu1",
    }
    _name_to_control_character = {
        "backspace": "",  # Is assigned later in `self._init_backspace_control_character`
        "ctrl-a": "\001",
        "ctrl-b": "\002",
        "ctrl-e": "\005",
        "ctrl-f": "\006",
        "ctrl-g": "\007",
        "ctrl-j": "\012",
        "ctrl-k": "\013",
        "ctrl-n": "\016",
        "ctrl-p": "\020",
        "enter": "\015",
        "escape": "\033",
        "tab": "\t",
    }
    _codenames = tuple(_codename_to_capname.keys())
    _codename_to_terminal_code = None  # type: Optional[Dict[str, str]]
    _terminal_code_to_codename = None  # type: Optional[Dict[str, str]]

    def __init__(
        self,
        menu_entries: Iterable[str],
        *,
        accept_keys: Iterable[str] = DEFAULT_ACCEPT_KEYS,
        clear_menu_on_exit: bool = DEFAULT_CLEAR_MENU_ON_EXIT,
        clear_screen: bool = DEFAULT_CLEAR_SCREEN,
        cursor_index: Optional[int] = None,
        cycle_cursor: bool = DEFAULT_CYCLE_CURSOR,
        exit_on_shortcut: bool = DEFAULT_EXIT_ON_SHORTCUT,
        menu_cursor: Optional[str] = DEFAULT_MENU_CURSOR,
        menu_cursor_style: Optional[Iterable[str]] = DEFAULT_MENU_CURSOR_STYLE,
        menu_highlight_style: Optional[Iterable[str]] = DEFAULT_MENU_HIGHLIGHT_STYLE,
        multi_select: bool = DEFAULT_MULTI_SELECT,
        multi_select_cursor: str = DEFAULT_MULTI_SELECT_CURSOR,
        multi_select_cursor_brackets_style: Optional[Iterable[str]] = DEFAULT_MULTI_SELECT_CURSOR_BRACKETS_STYLE,
        multi_select_cursor_style: Optional[Iterable[str]] = DEFAULT_MULTI_SELECT_CURSOR_STYLE,
        multi_select_empty_ok: bool = False,
        multi_select_keys: Optional[Iterable[str]] = DEFAULT_MULTI_SELECT_KEYS,
        multi_select_select_on_accept: bool = DEFAULT_MULTI_SELECT_SELECT_ON_ACCEPT,
        preselected_entries: Optional[Iterable[Union[str, int]]] = None,
        preview_border: bool = DEFAULT_PREVIEW_BORDER,
        preview_command: Optional[Union[str, Callable[[str], str]]] = None,
        preview_size: float = DEFAULT_PREVIEW_SIZE,
        preview_title: str = DEFAULT_PREVIEW_TITLE,
        quit_keys: Iterable[str] = DEFAULT_QUIT_KEYS,
        raise_error_on_interrupt: bool = False,
        search_case_sensitive: bool = DEFAULT_SEARCH_CASE_SENSITIVE,
        search_highlight_style: Optional[Iterable[str]] = DEFAULT_SEARCH_HIGHLIGHT_STYLE,
        search_key: Optional[str] = DEFAULT_SEARCH_KEY,
        shortcut_brackets_highlight_style: Optional[Iterable[str]] = DEFAULT_SHORTCUT_BRACKETS_HIGHLIGHT_STYLE,
        shortcut_key_highlight_style: Optional[Iterable[str]] = DEFAULT_SHORTCUT_KEY_HIGHLIGHT_STYLE,
        show_multi_select_hint: bool = DEFAULT_SHOW_MULTI_SELECT_HINT,
        show_multi_select_hint_text: Optional[str] = None,
        show_search_hint: bool = DEFAULT_SHOW_SEARCH_HINT,
        show_search_hint_text: Optional[str] = None,
        show_shortcut_hints: bool = DEFAULT_SHOW_SHORTCUT_HINTS,
        show_shortcut_hints_in_status_bar: bool = DEFAULT_SHOW_SHORTCUT_HINTS_IN_STATUS_BAR,
        skip_empty_entries: bool = False,
        status_bar: Optional[Union[str, Iterable[str], Callable[[str], str]]] = None,
        status_bar_below_preview: bool = DEFAULT_STATUS_BAR_BELOW_PREVIEW,
        status_bar_style: Optional[Iterable[str]] = DEFAULT_STATUS_BAR_STYLE,
        title: Optional[Union[str, Iterable[str]]] = None
    ):
        def check_for_terminal_environment() -> None:
            if "TERM" not in os.environ or os.environ["TERM"] == "":
                if "PYCHARM_HOSTED" in os.environ:
                    raise NotImplementedError(
                        "simple-term-menu does not work in the PyCharm output console. Use a terminal instead (Alt + "
                        'F12) or activate "Emulate terminal in output console".'
                    )
                raise NotImplementedError("simple-term-menu can only be used in a terminal emulator")

        def extract_shortcuts_menu_entries_and_preview_arguments(
            entries: Iterable[str],
        ) -> Tuple[List[str], List[Optional[str]], List[Optional[str]], List[int]]:
            separator_pattern = re.compile(r"([^\\])\|")
            escaped_separator_pattern = re.compile(r"\\\|")
            menu_entry_pattern = re.compile(r"^(?:\[(\S)\]\s*)?([^\x1F]+)(?:\x1F([^\x1F]*))?")
            shortcut_keys = []  # type: List[Optional[str]]
            menu_entries = []  # type: List[str]
            preview_arguments = []  # type: List[Optional[str]]
            skip_indices = []  # type: List[int]

            for idx, entry in enumerate(entries):
                if entry is None or (entry == "" and skip_empty_entries):
                    shortcut_keys.append(None)
                    menu_entries.append("")
                    preview_arguments.append(None)
                    skip_indices.append(idx)
                else:
                    unit_separated_entry = escaped_separator_pattern.sub("|", separator_pattern.sub("\\1\x1F", entry))
                    match_obj = menu_entry_pattern.match(unit_separated_entry)
                    # this is none in case the entry was an emtpy string which
                    # will be interpreted as a separator
                    assert match_obj is not None
                    shortcut_key = match_obj.group(1)
                    display_text = match_obj.group(2)
                    preview_argument = match_obj.group(3)
                    shortcut_keys.append(shortcut_key)
                    menu_entries.append(display_text)
                    preview_arguments.append(preview_argument)

            return menu_entries, shortcut_keys, preview_arguments, skip_indices

        def convert_preselected_entries_to_indices(
            preselected_indices_or_entries: Iterable[Union[str, int]]
        ) -> Set[int]:
            menu_entry_to_indices = {}  # type: Dict[str, Set[int]]
            for menu_index, menu_entry in enumerate(self._menu_entries):
                menu_entry_to_indices.setdefault(menu_entry, set())
                menu_entry_to_indices[menu_entry].add(menu_index)
            preselected_indices = set()
            for item in preselected_indices_or_entries:
                if isinstance(item, int):
                    if 0 <= item < len(self._menu_entries):
                        preselected_indices.add(item)
                    else:
                        raise IndexError(
                            "Error: {} is outside the allowable range of 0..{}.".format(
                                item, len(self._menu_entries) - 1
                            )
                        )
                elif isinstance(item, str):
                    try:
                        preselected_indices.update(menu_entry_to_indices[item])
                    except KeyError as e:
                        raise UnknownMenuEntryError('Pre-selection "{}" is not a valid menu entry.'.format(item)) from e
                else:
                    raise ValueError('"preselected_entries" must either contain integers or strings.')
            return preselected_indices

        def setup_title_or_status_bar_lines(
            title_or_status_bar: Optional[Union[str, Iterable[str]]],
            show_shortcut_hints: bool,
            menu_entries: Iterable[str],
            shortcut_keys: Iterable[Optional[str]],
            shortcut_hints_in_parentheses: bool,
        ) -> Tuple[str, ...]:
            if title_or_status_bar is None:
                lines = []  # type: List[str]
            elif isinstance(title_or_status_bar, str):
                lines = title_or_status_bar.split("\n")
            else:
                lines = list(title_or_status_bar)
            if show_shortcut_hints:
                shortcut_hints_line = self._get_shortcut_hints_line(
                    menu_entries, shortcut_keys, shortcut_hints_in_parentheses
                )
                if shortcut_hints_line is not None:
                    lines.append(shortcut_hints_line)
            return tuple(lines)

        check_for_terminal_environment()
        (
            self._menu_entries,
            self._shortcut_keys,
            self._preview_arguments,
            self._skip_indices,
        ) = extract_shortcuts_menu_entries_and_preview_arguments(menu_entries)
        self._shortcuts_defined = any(key is not None for key in self._shortcut_keys)
        self._accept_keys = tuple(accept_keys)
        self._clear_menu_on_exit = clear_menu_on_exit
        self._clear_screen = clear_screen
        self._cycle_cursor = cycle_cursor
        self._multi_select_empty_ok = multi_select_empty_ok
        self._exit_on_shortcut = exit_on_shortcut
        self._menu_cursor = menu_cursor if menu_cursor is not None else ""
        self._menu_cursor_style = tuple(menu_cursor_style) if menu_cursor_style is not None else ()
        self._menu_highlight_style = tuple(menu_highlight_style) if menu_highlight_style is not None else ()
        self._multi_select = multi_select
        self._multi_select_cursor = multi_select_cursor
        self._multi_select_cursor_brackets_style = (
            tuple(multi_select_cursor_brackets_style) if multi_select_cursor_brackets_style is not None else ()
        )
        self._multi_select_cursor_style = (
            tuple(multi_select_cursor_style) if multi_select_cursor_style is not None else ()
        )
        self._multi_select_keys = tuple(multi_select_keys) if multi_select_keys is not None else ()
        self._multi_select_select_on_accept = multi_select_select_on_accept
        if preselected_entries and not self._multi_select:
            raise InvalidParameterCombinationError(
                "Multi-select mode must be enabled when preselected entries are given."
            )
        self._preselected_indices = (
            convert_preselected_entries_to_indices(preselected_entries) if preselected_entries is not None else None
        )
        self._preview_border = preview_border
        self._preview_command = preview_command
        self._preview_size = preview_size
        self._preview_title = preview_title
        self._quit_keys = tuple(quit_keys)
        self._raise_error_on_interrupt = raise_error_on_interrupt
        self._search_case_sensitive = search_case_sensitive
        self._search_highlight_style = tuple(search_highlight_style) if search_highlight_style is not None else ()
        self._search_key = search_key
        self._shortcut_brackets_highlight_style = (
            tuple(shortcut_brackets_highlight_style) if shortcut_brackets_highlight_style is not None else ()
        )
        self._shortcut_key_highlight_style = (
            tuple(shortcut_key_highlight_style) if shortcut_key_highlight_style is not None else ()
        )
        self._show_search_hint = show_search_hint
        self._show_search_hint_text = show_search_hint_text
        self._show_shortcut_hints = show_shortcut_hints
        self._show_shortcut_hints_in_status_bar = show_shortcut_hints_in_status_bar
        self._status_bar_func = None  # type: Optional[Callable[[str], str]]
        self._status_bar_lines = None  # type: Optional[Tuple[str, ...]]
        if callable(status_bar):
            self._status_bar_func = status_bar
        else:
            self._status_bar_lines = setup_title_or_status_bar_lines(
                status_bar,
                show_shortcut_hints and show_shortcut_hints_in_status_bar,
                self._menu_entries,
                self._shortcut_keys,
                False,
            )
        self._status_bar_below_preview = status_bar_below_preview
        self._status_bar_style = tuple(status_bar_style) if status_bar_style is not None else ()
        self._title_lines = setup_title_or_status_bar_lines(
            title,
            show_shortcut_hints and not show_shortcut_hints_in_status_bar,
            self._menu_entries,
            self._shortcut_keys,
            True,
        )
        self._show_multi_select_hint = show_multi_select_hint
        self._show_multi_select_hint_text = show_multi_select_hint_text
        self._chosen_accept_key = None  # type: Optional[str]
        self._chosen_menu_index = None  # type: Optional[int]
        self._chosen_menu_indices = None  # type: Optional[Tuple[int, ...]]
        self._paint_before_next_read = False
        self._previous_displayed_menu_height = None  # type: Optional[int]
        self._reading_next_key = False
        self._search = self.Search(
            self._menu_entries,
            case_senitive=self._search_case_sensitive,
            show_search_hint=self._show_search_hint,
        )
        self._selection = self.Selection(self._preselected_indices)
        self._viewport = self.Viewport(
            len(self._menu_entries),
            len(self._title_lines),
            len(self._status_bar_lines) if self._status_bar_lines is not None else 0,
            0,
            0,
        )
        self._view = self.View(
            self._menu_entries, self._search, self._selection, self._viewport, self._cycle_cursor, self._skip_indices
        )
        if cursor_index and 0 < cursor_index < len(self._menu_entries):
            self._view.active_menu_index = cursor_index
        self._search.change_callback = self._view.update_view
        self._old_term = None  # type: Optional[List[Union[int, List[bytes]]]]
        self._new_term = None  # type: Optional[List[Union[int, List[bytes]]]]
        self._tty_in = None  # type: Optional[TextIO]
        self._tty_out = None  # type: Optional[TextIO]
        self._user_locale = get_locale()
        self._check_for_valid_styles()
        # backspace can be queried from the terminal database but is unreliable, query the terminal directly instead
        self._init_backspace_control_character()
        self._add_missing_control_characters_for_keys(self._accept_keys)
        self._add_missing_control_characters_for_keys(self._quit_keys)
        self._init_terminal_codes()

    @staticmethod
    def _get_shortcut_hints_line(
        menu_entries: Iterable[str],
        shortcut_keys: Iterable[Optional[str]],
        shortcut_hints_in_parentheses: bool,
    ) -> Optional[str]:
        shortcut_hints_line = ", ".join(
            "[{}]: {}".format(shortcut_key, menu_entry)
            for shortcut_key, menu_entry in zip(shortcut_keys, menu_entries)
            if shortcut_key is not None
        )
        if shortcut_hints_line != "":
            if shortcut_hints_in_parentheses:
                return "(" + shortcut_hints_line + ")"
            else:
                return shortcut_hints_line
        return None

    @staticmethod
    def _get_keycode_for_key(key: str) -> str:
        if len(key) == 1:
            # One letter keys represent themselves
            return key
        alt_modified_regex = re.compile(r"[Aa]lt-(\S)")
        ctrl_modified_regex = re.compile(r"[Cc]trl-(\S)")
        match_obj = alt_modified_regex.match(key)
        if match_obj:
            return "\033" + match_obj.group(1)
        match_obj = ctrl_modified_regex.match(key)
        if match_obj:
            # Ctrl + key is interpreted by terminals as the ascii code of that key minus 64
            ctrl_code_ascii = ord(match_obj.group(1).upper()) - 64
            if ctrl_code_ascii < 0:
                # Interpret negative ascii codes as unsigned 7-Bit integers
                ctrl_code_ascii = ctrl_code_ascii & 0x80 - 1
            return chr(ctrl_code_ascii)
        raise ValueError('Cannot interpret the given key "{}".'.format(key))

    @classmethod
    def _init_backspace_control_character(self) -> None:
        try:
            with open("/dev/tty", "r") as tty:
                stty_output = subprocess.check_output(["stty", "-a"], universal_newlines=True, stdin=tty)
            name_to_keycode_regex = re.compile(r"^\s*(\S+)\s*=\s*\^(\S+)\s*$")
            for field in stty_output.split(";"):
                match_obj = name_to_keycode_regex.match(field)
                if not match_obj:
                    continue
                name, ctrl_code = match_obj.group(1), match_obj.group(2)
                if name != "erase":
                    continue
                self._name_to_control_character["backspace"] = self._get_keycode_for_key("ctrl-" + ctrl_code)
                return
        except subprocess.CalledProcessError:
            pass
        # Backspace control character could not be queried, assume `<Ctrl-?>` (is most often used)
        self._name_to_control_character["backspace"] = "\177"

    @classmethod
    def _add_missing_control_characters_for_keys(cls, keys: Iterable[str]) -> None:
        for key in keys:
            if key not in cls._name_to_control_character and key not in string.ascii_letters:
                cls._name_to_control_character[key] = cls._get_keycode_for_key(key)

    @classmethod
    def _init_terminal_codes(cls) -> None:
        if cls._codename_to_terminal_code is not None:
            return
        supported_colors = int(cls._query_terminfo_database("colors"))
        cls._codename_to_terminal_code = {
            codename: (
                cls._query_terminfo_database(codename)
                if not (codename.startswith("bg_") or codename.startswith("fg_")) or supported_colors >= 8
                else ""
            )
            for codename in cls._codenames
        }
        cls._codename_to_terminal_code.update(cls._name_to_control_character)
        cls._terminal_code_to_codename = {
            terminal_code: codename for codename, terminal_code in cls._codename_to_terminal_code.items()
        }

    @classmethod
    def _query_terminfo_database(cls, codename: str) -> str:
        if codename in cls._codename_to_capname:
            capname = cls._codename_to_capname[codename]
        else:
            capname = codename
        try:
            return subprocess.check_output(["tput"] + capname.split(), universal_newlines=True)
        except subprocess.CalledProcessError as e:
            # The return code 1 indicates a missing terminal capability
            if e.returncode == 1:
                return ""
            raise e

    @classmethod
    def _num_lines(self) -> int:
        return int(self._query_terminfo_database("lines"))

    @classmethod
    def _num_cols(self) -> int:
        return int(self._query_terminfo_database("cols"))

    def _check_for_valid_styles(self) -> None:
        invalid_styles = []
        for style_tuple in (
            self._menu_cursor_style,
            self._menu_highlight_style,
            self._search_highlight_style,
            self._shortcut_key_highlight_style,
            self._shortcut_brackets_highlight_style,
            self._status_bar_style,
            self._multi_select_cursor_brackets_style,
            self._multi_select_cursor_style,
        ):
            for style in style_tuple:
                if style not in self._codename_to_capname:
                    invalid_styles.append(style)
        if invalid_styles:
            if len(invalid_styles) == 1:
                raise InvalidStyleError('The style "{}" does not exist.'.format(invalid_styles[0]))
            else:
                raise InvalidStyleError('The styles ("{}") do not exist.'.format('", "'.join(invalid_styles)))

    def _init_term(self) -> None:
        # pylint: disable=unsubscriptable-object
        assert self._codename_to_terminal_code is not None
        self._tty_in = open("/dev/tty", "r", encoding=self._user_locale)
        self._tty_out = open("/dev/tty", "w", encoding=self._user_locale, errors="replace")
        self._old_term = termios.tcgetattr(self._tty_in.fileno())
        self._new_term = termios.tcgetattr(self._tty_in.fileno())
        # set the terminal to: no line-buffering, no echo and no <CR> to <NL> translation (so <enter> sends <CR> instead
        # of <NL, this is necessary to distinguish between <enter> and <Ctrl-j> since <Ctrl-j> generates <NL>)
        self._new_term[3] = cast(int, self._new_term[3]) & ~termios.ICANON & ~termios.ECHO & ~termios.ICRNL
        self._new_term[0] = cast(int, self._new_term[0]) & ~termios.ICRNL
        # Set the timings for an unbuffered read: Return immediately after at least one character has arrived and don't
        # wait for further characters
        cast(List[bytes], self._new_term[6])[termios.VMIN] = b"\x01"
        cast(List[bytes], self._new_term[6])[termios.VTIME] = b"\x00"
        termios.tcsetattr(
            self._tty_in.fileno(), termios.TCSAFLUSH, cast(List[Union[int, List[Union[bytes, int]]]], self._new_term)
        )
        # Enter terminal application mode to get expected escape codes for arrow keys
        self._tty_out.write(self._codename_to_terminal_code["enter_application_mode"])
        self._tty_out.write(self._codename_to_terminal_code["cursor_invisible"])
        if self._clear_screen:
            self._tty_out.write(self._codename_to_terminal_code["clear"])

    def _reset_term(self) -> None:
        # pylint: disable=unsubscriptable-object
        assert self._codename_to_terminal_code is not None
        assert self._tty_in is not None
        assert self._tty_out is not None
        assert self._old_term is not None
        termios.tcsetattr(
            self._tty_out.fileno(), termios.TCSAFLUSH, cast(List[Union[int, List[Union[bytes, int]]]], self._old_term)
        )
        self._tty_out.write(self._codename_to_terminal_code["cursor_visible"])
        self._tty_out.write(self._codename_to_terminal_code["exit_application_mode"])
        if self._clear_screen:
            self._tty_out.write(self._codename_to_terminal_code["clear"])
        self._tty_in.close()
        self._tty_out.close()

    def _paint_menu(self) -> None:
        def get_status_bar_lines() -> Tuple[str, ...]:
            def get_multi_select_hint() -> str:
                def get_string_from_keys(keys: Sequence[str]) -> str:
                    string_to_key = {
                        " ": "space",
                    }
                    keys_string = ", ".join(
                        "<" + string_to_key.get(accept_key, accept_key) + ">" for accept_key in keys
                    )
                    return keys_string

                accept_keys_string = get_string_from_keys(self._accept_keys)
                multi_select_keys_string = get_string_from_keys(self._multi_select_keys)
                if self._show_multi_select_hint_text is not None:
                    return self._show_multi_select_hint_text.format(
                        multi_select_keys=multi_select_keys_string, accept_keys=accept_keys_string
                    )
                else:
                    return "Press {} for multi-selection and {} to {}accept".format(
                        multi_select_keys_string,
                        accept_keys_string,
                        "select and " if self._multi_select_select_on_accept else "",
                    )

            if self._status_bar_func is not None and self._view.active_menu_index is not None:
                status_bar_lines = tuple(
                    self._status_bar_func(self._menu_entries[self._view.active_menu_index]).strip().split("\n")
                )
                if self._show_shortcut_hints and self._show_shortcut_hints_in_status_bar:
                    shortcut_hints_line = self._get_shortcut_hints_line(self._menu_entries, self._shortcut_keys, False)
                    if shortcut_hints_line is not None:
                        status_bar_lines += (shortcut_hints_line,)
            elif self._status_bar_lines is not None:
                status_bar_lines = self._status_bar_lines
            else:
                status_bar_lines = tuple()
            if self._multi_select and self._show_multi_select_hint:
                status_bar_lines += (get_multi_select_hint(),)
            return status_bar_lines

        def apply_style(
            style_iterable: Optional[Iterable[str]] = None, reset: bool = True, file: Optional[TextIO] = None
        ) -> None:
            # pylint: disable=unsubscriptable-object
            assert self._codename_to_terminal_code is not None
            assert self._tty_out is not None
            if file is None:
                file = self._tty_out
            if reset or style_iterable is None:
                file.write(self._codename_to_terminal_code["reset_attributes"])
            if style_iterable is not None:
                for style in style_iterable:
                    file.write(self._codename_to_terminal_code[style])

        def print_menu_entries() -> int:
            # pylint: disable=unsubscriptable-object
            assert self._codename_to_terminal_code is not None
            assert self._tty_out is not None
            all_cursors_width = wcswidth(self._menu_cursor) + (
                wcswidth(self._multi_select_cursor) if self._multi_select else 0
            )
            current_menu_block_displayed_height = 0  # sum all written lines
            num_cols = self._num_cols()
            if self._title_lines:
                self._tty_out.write(
                    len(self._title_lines) * self._codename_to_terminal_code["cursor_up"]
                    + "\r"
                    + "\n".join(
                        (title_line[:num_cols] + (num_cols - wcswidth(title_line)) * " ")
                        for title_line in self._title_lines
                    )
                    + "\n"
                )
            shortcut_string_len = 4 if self._shortcuts_defined else 0
            displayed_index = -1
            for displayed_index, menu_index, menu_entry in self._view:
                current_shortcut_key = self._shortcut_keys[menu_index]
                self._tty_out.write(all_cursors_width * self._codename_to_terminal_code["cursor_right"])
                if self._shortcuts_defined:
                    if current_shortcut_key is not None:
                        apply_style(self._shortcut_brackets_highlight_style)
                        self._tty_out.write("[")
                        apply_style(self._shortcut_key_highlight_style)
                        self._tty_out.write(current_shortcut_key)
                        apply_style(self._shortcut_brackets_highlight_style)
                        self._tty_out.write("]")
                        apply_style()
                    else:
                        self._tty_out.write(3 * " ")
                    self._tty_out.write(" ")
                if menu_index == self._view.active_menu_index:
                    apply_style(self._menu_highlight_style)
                if self._search and self._search.search_text != "":
                    match_obj = self._search.matches[displayed_index][1]
                    self._tty_out.write(
                        menu_entry[: min(match_obj.start(), num_cols - all_cursors_width - shortcut_string_len)]
                    )
                    apply_style(self._search_highlight_style)
                    self._tty_out.write(
                        menu_entry[
                            match_obj.start() : min(match_obj.end(), num_cols - all_cursors_width - shortcut_string_len)
                        ]
                    )
                    apply_style()
                    if menu_index == self._view.active_menu_index:
                        apply_style(self._menu_highlight_style)
                    self._tty_out.write(
                        menu_entry[match_obj.end() : num_cols - all_cursors_width - shortcut_string_len]
                    )
                else:
                    self._tty_out.write(menu_entry[: num_cols - all_cursors_width - shortcut_string_len])
                if menu_index == self._view.active_menu_index:
                    apply_style()
                self._tty_out.write((num_cols - wcswidth(menu_entry) - all_cursors_width - shortcut_string_len) * " ")
                if displayed_index < self._viewport.upper_index:
                    self._tty_out.write("\n")
            empty_menu_lines = self._viewport.upper_index - displayed_index
            self._tty_out.write(
                max(0, empty_menu_lines - 1) * (num_cols * " " + "\n") + min(1, empty_menu_lines) * (num_cols * " ")
            )
            self._tty_out.write("\r" + (self._viewport.size - 1) * self._codename_to_terminal_code["cursor_up"])
            current_menu_block_displayed_height += self._viewport.size - 1  # sum all written lines
            return current_menu_block_displayed_height

        def print_search_line(current_menu_height: int) -> int:
            # pylint: disable=unsubscriptable-object
            assert self._codename_to_terminal_code is not None
            assert self._tty_out is not None
            current_menu_block_displayed_height = 0
            num_cols = self._num_cols()
            if self._search or self._show_search_hint:
                self._tty_out.write((current_menu_height + 1) * self._codename_to_terminal_code["cursor_down"])
            if self._search:
                assert self._search.search_text is not None
                self._tty_out.write(
                    (
                        (self._search_key if self._search_key is not None else DEFAULT_SEARCH_KEY)
                        + self._search.search_text
                    )[:num_cols]
                )
                self._tty_out.write((num_cols - len(self._search) - 1) * " ")
            elif self._show_search_hint:
                if self._show_search_hint_text is not None:
                    search_hint = self._show_search_hint_text.format(key=self._search_key)[:num_cols]
                elif self._search_key is not None:
                    search_hint = '(Press "{key}" to search)'.format(key=self._search_key)[:num_cols]
                else:
                    search_hint = "(Press any letter key to search)"[:num_cols]
                self._tty_out.write(search_hint)
                self._tty_out.write((num_cols - wcswidth(search_hint)) * " ")
            if self._search or self._show_search_hint:
                self._tty_out.write("\r" + (current_menu_height + 1) * self._codename_to_terminal_code["cursor_up"])
                current_menu_block_displayed_height = 1
            return current_menu_block_displayed_height

        def print_status_bar(current_menu_height: int, status_bar_lines: Tuple[str, ...]) -> int:
            # pylint: disable=unsubscriptable-object
            assert self._codename_to_terminal_code is not None
            assert self._tty_out is not None
            current_menu_block_displayed_height = 0  # sum all written lines
            num_cols = self._num_cols()
            if status_bar_lines:
                self._tty_out.write((current_menu_height + 1) * self._codename_to_terminal_code["cursor_down"])
                apply_style(self._status_bar_style)
                self._tty_out.write(
                    "\r"
                    + "\n".join(
                        (status_bar_line[:num_cols] + (num_cols - wcswidth(status_bar_line)) * " ")
                        for status_bar_line in status_bar_lines
                    )
                    + "\r"
                )
                apply_style()
                self._tty_out.write(
                    (current_menu_height + len(status_bar_lines)) * self._codename_to_terminal_code["cursor_up"]
                )
                current_menu_block_displayed_height += len(status_bar_lines)
            return current_menu_block_displayed_height

        def print_preview(current_menu_height: int, preview_max_num_lines: int) -> int:
            # pylint: disable=unsubscriptable-object
            assert self._codename_to_terminal_code is not None
            assert self._tty_out is not None
            if self._preview_command is None or preview_max_num_lines < 3:
                return 0

            def get_preview_string() -> Optional[str]:
                assert self._preview_command is not None
                if self._view.active_menu_index is None:
                    return None
                preview_argument = (
                    self._preview_arguments[self._view.active_menu_index]
                    if self._preview_arguments[self._view.active_menu_index] is not None
                    else self._menu_entries[self._view.active_menu_index]
                )
                if preview_argument == "":
                    return None
                if isinstance(self._preview_command, str):
                    try:
                        preview_process = subprocess.Popen(
                            [cmd_part.format(preview_argument) for cmd_part in shlex.split(self._preview_command)],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )
                        assert preview_process.stdout is not None
                        preview_string = (
                            io.TextIOWrapper(preview_process.stdout, encoding=self._user_locale, errors="replace")
                            .read()
                            .strip()
                        )
                    except subprocess.CalledProcessError as e:
                        raise PreviewCommandFailedError(
                            e.stderr.decode(encoding=self._user_locale, errors="replace").strip()
                        ) from e
                else:
                    preview_string = self._preview_command(preview_argument) if preview_argument is not None else ""
                return preview_string

            @static_variables(
                # Regex taken from https://stackoverflow.com/a/14693789/5958465
                ansi_escape_regex=re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"),
                # Modified version of https://stackoverflow.com/a/2188410/5958465
                ansi_sgr_regex=re.compile(r"\x1B\[[;\d]*m"),
            )
            def strip_ansi_codes_except_styling(string: str) -> str:
                stripped_string = strip_ansi_codes_except_styling.ansi_escape_regex.sub(  # type: ignore
                    lambda match_obj: (
                        match_obj.group(0)
                        if strip_ansi_codes_except_styling.ansi_sgr_regex.match(match_obj.group(0))  # type: ignore
                        else ""
                    ),
                    string,
                )
                return cast(str, stripped_string)

            @static_variables(
                regular_text_regex=re.compile(r"([^\x1B]+)(.*)"),
                ansi_escape_regex=re.compile(r"(\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]))(.*)"),
            )
            def limit_string_with_escape_codes(string: str, max_len: int) -> Tuple[str, int]:
                if max_len <= 0:
                    return "", 0
                string_parts = []
                string_len = 0
                while string:
                    regular_text_match = limit_string_with_escape_codes.regular_text_regex.match(string)  # type: ignore
                    if regular_text_match is not None:
                        regular_text = regular_text_match.group(1)
                        regular_text_len = wcswidth(regular_text)
                        if string_len + regular_text_len > max_len:
                            string_parts.append(regular_text[: max_len - string_len])
                            string_len = max_len
                            break
                        string_parts.append(regular_text)
                        string_len += regular_text_len
                        string = regular_text_match.group(2)
                    else:
                        ansi_escape_match = limit_string_with_escape_codes.ansi_escape_regex.match(  # type: ignore
                            string
                        )
                        if ansi_escape_match is not None:
                            # Adopt the ansi escape code but do not count its length
                            ansi_escape_code_text = ansi_escape_match.group(1)
                            string_parts.append(ansi_escape_code_text)
                            string = ansi_escape_match.group(2)
                        else:
                            # It looks like an escape code (starts with escape), but it is something else
                            # -> skip the escape character and continue the loop
                            string_parts.append("\x1B")
                            string = string[1:]
                return "".join(string_parts), string_len

            num_cols = self._num_cols()
            try:
                preview_string = get_preview_string()
                if preview_string is not None:
                    preview_string = strip_ansi_codes_except_styling(preview_string)
            except PreviewCommandFailedError as e:
                preview_string = "The preview command failed with error message:\n\n" + str(e)
            self._tty_out.write(current_menu_height * self._codename_to_terminal_code["cursor_down"])
            if preview_string is not None:
                self._tty_out.write(self._codename_to_terminal_code["cursor_down"] + "\r")
                if self._preview_border:
                    self._tty_out.write(
                        (
                            BoxDrawingCharacters.upper_left
                            + (2 * BoxDrawingCharacters.horizontal + " " + self._preview_title)[: num_cols - 3]
                            + " "
                            + (num_cols - wcswidth(self._preview_title) - 6) * BoxDrawingCharacters.horizontal
                            + BoxDrawingCharacters.upper_right
                        )[:num_cols]
                        + "\n"
                    )
                # `finditer` can be used as a generator version of `str.join`
                for i, line in enumerate(
                    match.group(0) for match in re.finditer(r"^.*$", preview_string, re.MULTILINE)
                ):
                    if i >= preview_max_num_lines - (2 if self._preview_border else 0):
                        preview_num_lines = preview_max_num_lines
                        break
                    limited_line, limited_line_len = limit_string_with_escape_codes(
                        line, num_cols - (3 if self._preview_border else 0)
                    )
                    self._tty_out.write(
                        (
                            ((BoxDrawingCharacters.vertical + " ") if self._preview_border else "")
                            + limited_line
                            + self._codename_to_terminal_code["reset_attributes"]
                            + max(num_cols - limited_line_len - (3 if self._preview_border else 0), 0) * " "
                            + (BoxDrawingCharacters.vertical if self._preview_border else "")
                        )
                    )
                else:
                    preview_num_lines = i + (3 if self._preview_border else 1)
                if self._preview_border:
                    self._tty_out.write(
                        "\n"
                        + (
                            BoxDrawingCharacters.lower_left
                            + (num_cols - 2) * BoxDrawingCharacters.horizontal
                            + BoxDrawingCharacters.lower_right
                        )[:num_cols]
                    )
                self._tty_out.write("\r")
            else:
                preview_num_lines = 0
            self._tty_out.write(
                (current_menu_height + preview_num_lines) * self._codename_to_terminal_code["cursor_up"]
            )
            return preview_num_lines

        def delete_old_menu_lines(displayed_menu_height: int) -> None:
            # pylint: disable=unsubscriptable-object
            assert self._codename_to_terminal_code is not None
            assert self._tty_out is not None
            if (
                self._previous_displayed_menu_height is not None
                and self._previous_displayed_menu_height > displayed_menu_height
            ):
                self._tty_out.write((displayed_menu_height + 1) * self._codename_to_terminal_code["cursor_down"])
                self._tty_out.write(
                    (self._previous_displayed_menu_height - displayed_menu_height)
                    * self._codename_to_terminal_code["delete_line"]
                )
                self._tty_out.write((displayed_menu_height + 1) * self._codename_to_terminal_code["cursor_up"])

        def position_cursor() -> None:
            # pylint: disable=unsubscriptable-object
            assert self._codename_to_terminal_code is not None
            assert self._tty_out is not None
            if self._view.active_displayed_index is None:
                return

            cursor_width = wcswidth(self._menu_cursor)
            for displayed_index in range(self._viewport.lower_index, self._viewport.upper_index + 1):
                if displayed_index == self._view.active_displayed_index:
                    apply_style(self._menu_cursor_style)
                    self._tty_out.write(self._menu_cursor)
                    apply_style()
                else:
                    self._tty_out.write(cursor_width * " ")
                self._tty_out.write("\r")
                if displayed_index < self._viewport.upper_index:
                    self._tty_out.write(self._codename_to_terminal_code["cursor_down"])
            self._tty_out.write((self._viewport.size - 1) * self._codename_to_terminal_code["cursor_up"])

        def print_multi_select_column() -> None:
            # pylint: disable=unsubscriptable-object
            assert self._codename_to_terminal_code is not None
            assert self._tty_out is not None
            if not self._multi_select:
                return

            def prepare_multi_select_cursors() -> Tuple[str, str]:
                bracket_characters = "([{<)]}>"
                bracket_style_escape_codes_io = io.StringIO()
                multi_select_cursor_style_escape_codes_io = io.StringIO()
                reset_codes_io = io.StringIO()
                apply_style(self._multi_select_cursor_brackets_style, file=bracket_style_escape_codes_io)
                apply_style(self._multi_select_cursor_style, file=multi_select_cursor_style_escape_codes_io)
                apply_style(file=reset_codes_io)
                bracket_style_escape_codes = bracket_style_escape_codes_io.getvalue()
                multi_select_cursor_style_escape_codes = multi_select_cursor_style_escape_codes_io.getvalue()
                reset_codes = reset_codes_io.getvalue()

                cursor_with_brackets_only = re.sub(
                    r"[^{}]".format(re.escape(bracket_characters)), " ", self._multi_select_cursor
                )
                cursor_with_brackets_only_styled = re.sub(
                    r"[{}]+".format(re.escape(bracket_characters)),
                    lambda match_obj: bracket_style_escape_codes + match_obj.group(0) + reset_codes,
                    cursor_with_brackets_only,
                )
                cursor_styled = re.sub(
                    r"[{brackets}]+|[^{brackets}\s]+".format(brackets=re.escape(bracket_characters)),
                    lambda match_obj: (
                        bracket_style_escape_codes
                        if match_obj.group(0)[0] in bracket_characters
                        else multi_select_cursor_style_escape_codes
                    )
                    + match_obj.group(0)
                    + reset_codes,
                    self._multi_select_cursor,
                )
                return cursor_styled, cursor_with_brackets_only_styled

            if not self._view:
                return
            checked_multi_select_cursor, unchecked_multi_select_cursor = prepare_multi_select_cursors()
            cursor_width = wcswidth(self._menu_cursor)
            displayed_selected_indices = self._view.displayed_selected_indices
            displayed_index = 0
            for displayed_index, _, _ in self._view:
                self._tty_out.write("\r" + cursor_width * self._codename_to_terminal_code["cursor_right"])
                if displayed_index in self._skip_indices:
                    self._tty_out.write("")
                elif displayed_index in displayed_selected_indices:
                    self._tty_out.write(checked_multi_select_cursor)
                else:
                    self._tty_out.write(unchecked_multi_select_cursor)
                if displayed_index < self._viewport.upper_index:
                    self._tty_out.write(self._codename_to_terminal_code["cursor_down"])
            self._tty_out.write("\r")
            self._tty_out.write(
                (displayed_index + (1 if displayed_index < self._viewport.upper_index else 0))
                * self._codename_to_terminal_code["cursor_up"]
            )

        # pylint: disable=unsubscriptable-object
        assert self._codename_to_terminal_code is not None
        assert self._tty_out is not None
        displayed_menu_height = 0  # sum all written lines
        status_bar_lines = get_status_bar_lines()
        self._viewport.status_bar_lines_count = len(status_bar_lines)
        if self._preview_command is not None:
            self._viewport.preview_lines_count = int(self._preview_size * self._num_lines())
            preview_max_num_lines = self._viewport.preview_lines_count
        self._viewport.keep_visible(self._view.active_displayed_index)
        displayed_menu_height += print_menu_entries()
        displayed_menu_height += print_search_line(displayed_menu_height)
        if not self._status_bar_below_preview:
            displayed_menu_height += print_status_bar(displayed_menu_height, status_bar_lines)
        if self._preview_command is not None:
            displayed_menu_height += print_preview(displayed_menu_height, preview_max_num_lines)
        if self._status_bar_below_preview:
            displayed_menu_height += print_status_bar(displayed_menu_height, status_bar_lines)
        delete_old_menu_lines(displayed_menu_height)
        position_cursor()
        if self._multi_select:
            print_multi_select_column()
        self._previous_displayed_menu_height = displayed_menu_height
        self._tty_out.flush()

    def _clear_menu(self) -> None:
        # pylint: disable=unsubscriptable-object
        assert self._codename_to_terminal_code is not None
        assert self._previous_displayed_menu_height is not None
        assert self._tty_out is not None
        if self._clear_menu_on_exit:
            if self._title_lines:
                self._tty_out.write(len(self._title_lines) * self._codename_to_terminal_code["cursor_up"])
                self._tty_out.write(len(self._title_lines) * self._codename_to_terminal_code["delete_line"])
            self._tty_out.write(
                (self._previous_displayed_menu_height + 1) * self._codename_to_terminal_code["delete_line"]
            )
        else:
            self._tty_out.write(
                (self._previous_displayed_menu_height + 1) * self._codename_to_terminal_code["cursor_down"]
            )
        self._tty_out.flush()

    def _read_next_key(self, ignore_case: bool = True) -> str:
        # pylint: disable=unsubscriptable-object,unsupported-membership-test
        assert self._terminal_code_to_codename is not None
        assert self._tty_in is not None
        # Needed for asynchronous handling of terminal resize events
        self._reading_next_key = True
        if self._paint_before_next_read:
            self._paint_menu()
            self._paint_before_next_read = False
        # blocks until any amount of bytes is available
        code = os.read(self._tty_in.fileno(), 80).decode("utf-8", errors="ignore")
        self._reading_next_key = False
        if code in self._terminal_code_to_codename:
            return self._terminal_code_to_codename[code]
        elif ignore_case:
            return code.lower()
        else:
            return code

    def show(self) -> Optional[Union[int, Tuple[int, ...]]]:
        def init_signal_handling() -> None:
            # `SIGWINCH` is send on terminal resizes
            def handle_sigwinch(signum: int, frame: Optional[FrameType]) -> None:
                # pylint: disable=unused-argument
                if self._reading_next_key:
                    self._paint_menu()
                else:
                    self._paint_before_next_read = True

            signal.signal(signal.SIGWINCH, handle_sigwinch)

        def reset_signal_handling() -> None:
            signal.signal(signal.SIGWINCH, signal.SIG_DFL)

        def remove_letter_keys(menu_action_to_keys: Dict[str, Set[Optional[str]]]) -> None:
            letter_keys = frozenset(string.ascii_lowercase) | frozenset(" ")
            for keys in menu_action_to_keys.values():
                keys -= letter_keys

        # pylint: disable=unsubscriptable-object
        assert self._codename_to_terminal_code is not None
        self._init_term()
        if self._preselected_indices is None:
            self._selection.clear()
        self._chosen_accept_key = None
        self._chosen_menu_indices = None
        self._chosen_menu_index = None
        assert self._tty_out is not None
        if self._title_lines:
            # `print_menu` expects the cursor on the first menu item -> reserve one line for the title
            self._tty_out.write(len(self._title_lines) * self._codename_to_terminal_code["cursor_down"])
        menu_was_interrupted = False
        try:
            init_signal_handling()
            menu_action_to_keys = {
                "menu_up": set(("up", "ctrl-k", "ctrl-p", "k")),
                "menu_down": set(("down", "ctrl-j", "ctrl-n", "j")),
                "menu_page_up": set(("page_up", "ctrl-b")),
                "menu_page_down": set(("page_down", "ctrl-f")),
                "menu_start": set(("home", "ctrl-a")),
                "menu_end": set(("end", "ctrl-e")),
                "accept": set(self._accept_keys),
                "multi_select": set(self._multi_select_keys),
                "quit": set(self._quit_keys),
                "search_start": set((self._search_key,)),
                "backspace": set(("backspace",)),
            }  # type: Dict[str, Set[Optional[str]]]
            while True:
                self._paint_menu()
                current_menu_action_to_keys = copy.deepcopy(menu_action_to_keys)
                next_key = self._read_next_key(ignore_case=False)
                if self._search or self._search_key is None:
                    remove_letter_keys(current_menu_action_to_keys)
                else:
                    next_key = next_key.lower()
                if self._search_key is not None and not self._search and next_key in self._shortcut_keys:
                    shortcut_menu_index = self._shortcut_keys.index(next_key)
                    if self._exit_on_shortcut:
                        self._selection.add(shortcut_menu_index)
                        break
                    else:
                        if self._multi_select:
                            self._selection.toggle(shortcut_menu_index)
                        else:
                            self._view.active_menu_index = shortcut_menu_index
                elif next_key in current_menu_action_to_keys["menu_up"]:
                    self._view.decrement_active_index()
                elif next_key in current_menu_action_to_keys["menu_down"]:
                    self._view.increment_active_index()
                elif next_key in current_menu_action_to_keys["menu_page_up"]:
                    self._view.page_up()
                elif next_key in current_menu_action_to_keys["menu_page_down"]:
                    self._view.page_down()
                elif next_key in current_menu_action_to_keys["menu_start"]:
                    self._view.active_displayed_index = 0
                elif next_key in current_menu_action_to_keys["menu_end"]:
                    self._view.active_displayed_index = self._view.max_displayed_index
                elif self._multi_select and next_key in current_menu_action_to_keys["multi_select"]:
                    if self._view.active_menu_index is not None:
                        self._selection.toggle(self._view.active_menu_index)
                elif next_key in current_menu_action_to_keys["accept"]:
                    if self._view.active_menu_index is not None:
                        if (
                            self._multi_select_select_on_accept
                            or self._multi_select is False
                            or (not self._selection and self._multi_select_empty_ok is False)
                        ):
                            self._selection.add(self._view.active_menu_index)
                    self._chosen_accept_key = next_key
                    break
                elif next_key in current_menu_action_to_keys["quit"]:
                    if not self._search:
                        menu_was_interrupted = True
                        break
                    else:
                        self._search.search_text = None
                elif not self._search:
                    if next_key in current_menu_action_to_keys["search_start"] or (
                        self._search_key is None and next_key == DEFAULT_SEARCH_KEY
                    ):
                        self._search.search_text = ""
                    elif self._search_key is None:
                        self._search.search_text = next_key
                else:
                    assert self._search.search_text is not None
                    if next_key in ("backspace",):
                        if self._search.search_text != "":
                            self._search.search_text = self._search.search_text[:-1]
                        else:
                            self._search.search_text = None
                    elif wcswidth(next_key) >= 0 and not (
                        next_key in current_menu_action_to_keys["search_start"] and self._search.search_text == ""
                    ):
                        # Only append `next_key` if it is a printable character and the first character is not the
                        # `search_start` key
                        self._search.search_text += next_key
        except KeyboardInterrupt as e:
            if self._raise_error_on_interrupt:
                raise e
            menu_was_interrupted = True
        finally:
            reset_signal_handling()
            self._clear_menu()
            self._reset_term()
        if not menu_was_interrupted:
            chosen_menu_indices = self._selection.selected_menu_indices
            if chosen_menu_indices:
                if self._multi_select:
                    self._chosen_menu_indices = chosen_menu_indices
                else:
                    self._chosen_menu_index = chosen_menu_indices[0]
        return self._chosen_menu_indices if self._multi_select else self._chosen_menu_index

    @property
    def chosen_accept_key(self) -> Optional[str]:
        return self._chosen_accept_key

    @property
    def chosen_menu_entry(self) -> Optional[str]:
        return self._menu_entries[self._chosen_menu_index] if self._chosen_menu_index is not None else None

    @property
    def chosen_menu_entries(self) -> Optional[Tuple[str, ...]]:
        return (
            tuple(self._menu_entries[menu_index] for menu_index in self._chosen_menu_indices)
            if self._chosen_menu_indices is not None
            else None
        )

    @property
    def chosen_menu_index(self) -> Optional[int]:
        return self._chosen_menu_index

    @property
    def chosen_menu_indices(self) -> Optional[Tuple[int, ...]]:
        return self._chosen_menu_indices


class AttributeDict(dict):  # type: ignore
    def __getattr__(self, attr: str) -> Any:
        return self[attr]

    def __setattr__(self, attr: str, value: Any) -> None:
        self[attr] = value


#
# CLI HANDLING
#

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
    parser.add_argument('--overwrite', '-F', action='store_true', default=False,
                        help='overwrite already translated strings')
    parser.add_argument('--interactive', '-i', action='store_true', default=False,
                        help='resolve translation conflicts interactively')
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
