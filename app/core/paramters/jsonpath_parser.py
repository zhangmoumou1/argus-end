"""
jsonpath parser
"""
import json
from typing import Any

import jsonpath

from app.core.paramters.parser import Parser
from app.exception.error import CaseParametersError


class JSONPathParser(Parser):
    _MISSING = object()

    @classmethod
    def get_source(cls, source):
        return source.get("response")

    @classmethod
    def _lookup_plain_path(cls, data, expression: str):
        exp = (expression or "").strip()
        if not exp:
            return cls._MISSING
        if exp.startswith("$."):
            exp = exp[2:]
        if exp.startswith("$"):
            return cls._MISSING
        current = data
        for part in exp.split("."):
            if part == "":
                return cls._MISSING
            if isinstance(current, dict):
                if part not in current:
                    return cls._MISSING
                current = current.get(part)
                continue
            if isinstance(current, list) and part.isdigit():
                idx = int(part)
                if idx < 0 or idx >= len(current):
                    return cls._MISSING
                current = current[idx]
                continue
            return cls._MISSING
        return current

    @classmethod
    def parse(cls, source: dict, expression: str = "", **kwargs) -> Any:
        data = cls.get_source(source)
        if not source or not expression:
            raise CaseParametersError(f"parse out parameters failed, source or expression is empty")
        try:
            results = jsonpath.jsonpath(data, expression)
            if results is False:
                if not data and expression == "$..*":
                    # 说明想要全匹配并且没数据，直接返回data
                    return data
                # 兼容旧写法：expression 直接写 key / a.b / $.a.b
                fallback = cls._lookup_plain_path(data, expression)
                if fallback is not cls._MISSING:
                    return fallback
                raise CaseParametersError("jsonpath match failed, please check your response or jsonpath.")
            return Parser.parse_result(results, "0")
        except CaseParametersError as e:
            raise e
        except Exception as err:
            raise CaseParametersError(f"parse json data error, please check jsonpath or json: {err}")


class BodyJSONPathParser(JSONPathParser):
    @classmethod
    def get_source(cls, source):
        return source.get("request_data")
