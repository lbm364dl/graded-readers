"""Discover attested KRDict record URLs; discovery is never linguistic approval."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.parse import urlencode

from pipeline.korean_lexical_research import read_primary

BASIC_SEARCH_URL = 'https://krdict.korean.go.kr/eng/dicMarinerSearch/search'
BASIC_RECORD_URL = 'https://krdict.korean.go.kr/eng/dicSearch/SearchView'
_VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


class _ResultHeadings(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.current = None
        self.rows = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'a' and self.current is None:
            if (len(self.stack) >= 3 and self.stack[-1][0] == 'dt'
                    and self.stack[-2][0] == 'dl'
                    and 'search_result' in self.stack[-3][1].get('class', '').split()):
                match = re.fullmatch(r"javascript:checkSubmit\(\s*['\"]([1-9]\d*)['\"]\s*,\s*['\"]Y['\"]\s*\);?", attrs.get('href', ''))
                if match:
                    self.current = {'record_word_no': match[1], 'text': [], 'depth': len(self.stack)}
        if tag not in _VOID:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        if self.current and tag == 'a' and len(self.stack) == self.current['depth'] + 1:
            heading = re.sub(r'\s+', ' ', ''.join(self.current['text'])).strip()
            if heading:
                self.rows.append({'record_word_no': self.current['record_word_no'], 'headword': heading})
            self.current = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.current is None:
            return
        nested = self.stack[self.current['depth'] + 1:]
        if (not any(tag == 'sup' for tag, _ in nested)
                and any(tag == 'span' and any(re.fullmatch(r'word_type1_\d+', value)
                    for value in attrs.get('class', '').split()) for tag, attrs in nested)):
            self.current['text'].append(data)


def basic_result_records(content: str, headword: str) -> list[dict]:
    """Use exact result headings, excluding homonym superscripts and examples."""
    parser = _ResultHeadings()
    parser.feed(content)
    matches = {}
    for row in parser.rows:
        if row['headword'] == headword:
            number = row['record_word_no']
            matches[number] = {**row, 'primary_url': BASIC_RECORD_URL + '?' +
                urlencode({'ParaWordNo': number, 'nation': 'eng'})}
    return [matches[number] for number in sorted(matches, key=int)]


def basic_discovery(headword: str) -> dict:
    """Read the observed official lookup route and return exact-record leads."""
    if not isinstance(headword, str) or not headword.strip():
        raise ValueError('Discovery requires an exact nonempty dictionary headword')
    query = {'nation': 'eng', 'nationCode': '6', 'ParaWordNo': '', 'mainSearchWord': headword}
    url = BASIC_SEARCH_URL + '?' + urlencode(query)
    result = {'adapter_version': 1, 'headword_request': headword, 'search_url': url,
              'request_method': 'GET', 'request_parameters': query,
              'discovery_only': True, 'complete_dictionary_coverage': False,
              'records': []}
    try:
        content = read_primary(url)
        result.update(search_content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                      records=basic_result_records(content, headword))
    except (OSError, UnicodeError, HTTPException) as error:
        result['retrieval_error'] = type(error).__name__
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headword', required=True)
    args = parser.parse_args()
    print(json.dumps(basic_discovery(args.headword), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
