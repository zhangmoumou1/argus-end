from enum import IntEnum


class GConfigParserEnum(IntEnum):
    string = 0
    json = 1
    yaml = 2


class GconfigType(IntEnum):
    case = 0
    constructor = 1
    asserts = 2

    @staticmethod
    def text(val):
        if val == 0:
            return "用例"
        if val == 1:
            return "前后置条件"
        return "断言"


class GConfigVariableType(IntEnum):
    global_var = 1
    runtime_var = 2
    special_var = 3

    @staticmethod
    def text(val):
        if val == GConfigVariableType.global_var:
            return "全局变量"
        if val == GConfigVariableType.runtime_var:
            return "运行时变量"
        if val == GConfigVariableType.special_var:
            return "特殊变量"
        return "未知"
