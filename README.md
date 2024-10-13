<!--
SPDX-FileCopyrightText: 2023 Mirian Margiani
SPDX-License-Identifier: GFDL-1.3-or-later
-->

# Translations merger

This tool can merge Qt translations catalogs. It is intended as an eventual
replacement for the Bash script [opal-merge-translations.sh](https://github.com/Pretty-SFOS/opal/blob/main/snippets/opal-merge-translations.sh)
currently in use in Opal.


## Project status

This tool is usable and stable but it requires
[BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/bs4/doc). To be
included in Opal by default, it should be reworked to use only the Python
standard library.


## License

    Copyright (C) since 2022  Mirian Margiani
    Program: opal-merge-translations

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
