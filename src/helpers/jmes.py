import json
import re
from json.decoder import JSONDecodeError

import demjson3 as demjson
from itemloaders.processors import SelectJmes


class Jmes:
    RETRY_THRESHOLD = 20  # hopefully there won't be more than 20 issues in a single json

    def __init__(self, json_str: str, retry=0, encoded=False):
        if not isinstance(json_str, str) and not isinstance(json_str, bytes) and not isinstance(json_str, bytearray):
            raise TypeError('The input must be str, bytes or bytearray, not {!r}'.format(json_str.__class__.__name__))

        json_str = str(json_str)
        json_str = json_str.strip(' ;\r\n')

        try:
            self.json_obj = json.loads(json_str, strict=False)
        except JSONDecodeError as e:
            if 'Expecting property name enclosed in double quotes' == e.msg \
                    or 'Expecting value' == e.msg:
                # manage the case when the json is JavaScript valid
                try:
                    self.json_obj = demjson.decode(json_str)
                except demjson.JSONDecodeError as e:
                    if e.message.startswith("Can not decode value starting with character") and not encoded:
                        json_str = json_str.encode('utf-8').decode('unicode_escape')
                        self.__init__(json_str, retry + 1, True)
                    elif e.message == 'Values must be separated by a comma' and self.RETRY_THRESHOLD > retry:
                        str_section = json_str[e.position.char_position - 5:e.position.char_position + 1]
                        str_section_clean = re.sub(r"([^,:\[{\\])(')([^,\]}'])", r"\g<1>\\'\g<3>", str_section)

                        json_str = json_str.replace(str_section, str_section_clean)
                        if str_section == str_section_clean:
                            raise e
                        self.__init__(json_str, retry + 1, encoded)
                    else:
                        raise e
            elif 'Expecting' in e.msg and ('delimiter' in e.msg or '":"' in e.msg) and self.RETRY_THRESHOLD > retry:
                """
                The following lines of code try to handle the case when there are `"` inside the value
                e.g. {"key": "some 3" value"}
                """
                str_section = json_str[e.pos - 5:e.pos + 1]
                str_section_clean = re.sub(r'([^,:\[{\\])(")([^,\]}])', r'\g<1>\\"\g<3>', str_section)

                json_str = json_str.replace(str_section, str_section_clean)
                if str_section == str_section_clean:
                    raise e
                self.__init__(json_str, retry + 1, encoded)
            else:
                raise e

    def select(self, jpath, default=None):
        result = SelectJmes(jpath)(self.json_obj)

        if result is None or result == '':
            return default

        if isinstance(result, list):
            return result
        else:
            return str(result)

    def select_list(self, jpath: str, default=[]):
        result = SelectJmes(jpath)(self.json_obj)

        if result is None or result == '':
            return default

        if isinstance(result, list):
            return result
        else:
            return [result]

    def select_dict(self, jpath: str, default={}):
        result = SelectJmes(jpath)(self.json_obj)

        if result is None or result == '':
            return default

        if isinstance(result, dict):
            return result

        raise Exception('Selected value is not a dict')
