-- AI 测试父菜单（按现有菜单权限表结构调整字段名后执行）
-- code: ai-test, parent: 0, path: /ai-test, component: ./AITest
INSERT INTO menu (name, path, icon, component, parent_id, sort, status)
SELECT 'AI 测试', '/ai-test', 'robot', './AITest', 0, 90, 1
WHERE NOT EXISTS (SELECT 1 FROM menu WHERE path = '/ai-test');
