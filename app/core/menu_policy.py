MENU_ITEMS = [
    {"path": "/dashboard", "name": "\u4eea\u8868\u76d8", "children": [
        {"path": "/dashboard/workspace", "name": "\u5de5\u4f5c\u53f0"},
        {"path": "/dashboard/statistics", "name": "\u7edf\u8ba1\u5927\u76d8"},
    ]},
    {"path": "/project", "name": "\u9879\u76ee\u7ba1\u7406"},
    {"path": "/asset", "name": "\u63a5\u53e3\u8d44\u4ea7", "children": [
        {"path": "/asset/interface", "name": "\u670d\u52a1\u7ba1\u7406"},
        {"path": "/asset/mock", "name": "Mock\u670d\u52a1"},
        {"path": "/asset/record", "name": "\u63a5\u53e3\u5f55\u5236"},
    ]},
    {"path": "/scene-design", "name": "\u573a\u666f\u8bbe\u8ba1", "children": [
        {"path": "/scene-design/functionalCase", "name": "\u529f\u80fd\u7528\u4f8b"},
        {"path": "/scene-design/functionalSkill", "name": "\u7528\u4f8b\u6280\u80fd"},
    ]},
    {"path": "/scenario", "name": "\u63a5\u53e3\u6d4b\u8bd5", "children": [
        {"path": "/scenario/testcase", "name": "\u63a5\u53e3\u7528\u4f8b"},
        {"path": "/scenario/testplan", "name": "\u6d4b\u8bd5\u8ba1\u5212"},
        {"path": "/scenario/report", "name": "\u6d4b\u8bd5\u62a5\u544a"},
    ]},
    {"path": "/ui-test", "name": "UI\u6d4b\u8bd5", "children": [
        {"path": "/ui-test/cases", "name": "UI\u7528\u4f8b"},
        {"path": "/ui-test/plans", "name": "\u6d4b\u8bd5\u8ba1\u5212"},
        {"path": "/ui-test/runs", "name": "\u6d4b\u8bd5\u62a5\u544a"},
    ]},
    {"path": "/performance", "name": "\u6027\u80fd\u6d4b\u8bd5", "children": [
        {"path": "/performance/plan", "name": "\u6d4b\u8bd5\u8ba1\u5212"},
        {"path": "/performance/run", "name": "\u6267\u884c\u8bb0\u5f55"},
        {"path": "/performance/report", "name": "\u6d4b\u8bd5\u62a5\u544a"},
    ]},
    {"path": "/config", "name": "\u6d4b\u8bd5\u914d\u7f6e", "children": [
        {"path": "/config/environment", "name": "\u73af\u5883\u7ba1\u7406"},
        {"path": "/config/address", "name": "\u5730\u5740\u7ba1\u7406"},
        {"path": "/config/gconfig", "name": "\u5168\u5c40\u53d8\u91cf"},
        {"path": "/config/database", "name": "\u6570\u636e\u5e93\u914d\u7f6e"},
        {"path": "/config/redis", "name": "Redis\u914d\u7f6e"},
        {"path": "/config/mq", "name": "\u6d88\u606f\u4e2d\u95f4\u4ef6"},
        {"path": "/config/oss", "name": "\u5bf9\u8c61\u5b58\u50a8"},
    ]},
    {"path": "/system", "name": "\u540e\u53f0\u7ba1\u7406", "children": [
        {"path": "/system/configure", "name": "\u7cfb\u7edf\u8bbe\u7f6e"},
        {"path": "/system/model-config", "name": "\u6a21\u578b\u914d\u7f6e"},
        {"path": "/system/user", "name": "\u7528\u6237\u7ba1\u7406"},
        {"path": "/system/operation-log", "name": "\u64cd\u4f5c\u65e5\u5fd7"},
        {"path": "/system/notification-config", "name": "\u901a\u77e5\u914d\u7f6e"},
        {"path": "/system/menu", "name": "\u83dc\u5355\u7ba1\u7406"},
    ]},
    {"path": "/tool", "name": "\u5b9e\u7528\u5de5\u5177", "children": [
        {"path": "/tool/request", "name": "HTTP\u6d4b\u8bd5"},
        {"path": "/tool/sql", "name": "SQL\u5ba2\u6237\u7aef"},
        {"path": "/tool/redis", "name": "Redis\u5ba2\u6237\u7aef"},
        {"path": "/tool/kafka", "name": "Kafka Tools"},
        {"path": "/tool/rabbitmq", "name": "RabbitMQ Tools"},
    ]},
    {"path": "/datafactory", "name": "\u6570\u636e\u5de5\u5382"},
    {"path": "/ai-test", "name": "AI\u6d4b\u8bd5", "children": [
        {"path": "/ai-test/workbench", "name": "AI\u6d4b\u8bd5\u5de5\u4f5c\u53f0"},
        {"path": "/ai-test/projects", "name": "\u9879\u76ee\u4e0e\u7248\u672c"},
        {"path": "/ai-test/assets", "name": "\u9700\u6c42\u4e0e\u6d4b\u8bd5\u8d44\u4ea7"},
        {"path": "/ai-test/execution", "name": "\u6d4b\u8bd5\u6267\u884c"},
        {"path": "/ai-test/reports", "name": "\u62a5\u544a\u4e0e\u7f3a\u9677"},
        {"path": "/ai-test/capabilities", "name": "Skill\u4e0eMCP"},
    ]},
]

ROLES = [
    {"id": 0, "name": "普通用户"},
    {"id": 1, "name": "组长"},
    {"id": 2, "name": "超级管理员"},
]
RESERVED_PATH = "/system/menu"


def leaf_paths(items=MENU_ITEMS):
    return [path for item in items for path in (
        leaf_paths(item["children"]) if item.get("children") else [item["path"]]
    )]


def catalog_paths(items=MENU_ITEMS):
    return {item["path"] for item in items} | {
        path for item in items for path in catalog_paths(item.get("children", []))
    }


def default_paths(role):
    validate_role(role)
    return [path for path in leaf_paths() if role == 2 or not path.startswith("/system/")]


def validate_role(role):
    if role not in (0, 1, 2):
        raise ValueError("用户角色不合法")


def normalize_paths(role, paths):
    validate_role(role)
    selected = set(paths)
    if selected - catalog_paths():
        raise ValueError("包含不存在的菜单路径")
    if role != 2 and any(path == "/system" or path.startswith("/system/") for path in selected):
        raise ValueError("后台管理菜单仅供超级管理员使用")
    leaves = set()

    def collect(items, parent_selected=False):
        for item in items:
            checked = parent_selected or item["path"] in selected
            if item.get("children"):
                collect(item["children"], checked)
            elif checked:
                leaves.add(item["path"])

    collect(MENU_ITEMS)
    if role == 2:
        leaves.add(RESERVED_PATH)
    return [path for path in leaf_paths() if path in leaves]
