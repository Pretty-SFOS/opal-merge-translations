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

This tool is published under the [GPL-3.0-or-later](https://spdx.org/licenses/GPL-3.0-or-later.html) license:

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

The script bundles [simple-term-menu.py](https://github.com/IngoMeyer441/simple-term-menu)
which is available under the MIT license as follows:

    Copyright 2021 Forschungszentrum Jülich GmbH

    Permission is hereby granted, free of charge, to any person obtaining a copy of
    this software and associated documentation files (the "Software"), to deal in
    the Software without restriction, including without limitation the rights to
    use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
    the Software, and to permit persons to whom the Software is furnished to do so,
    subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
    FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
    COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
    IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
    CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
