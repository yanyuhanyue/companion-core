# 读取扩散（Read Amplification）优化

## 概述

本文档记录了对 Companion Core 代码库中读取扩散问题的系统性优化。读取扩散是指在事务开始时执行不必要的读取操作，导致 SQLite 创建过大的回滚日志，影响性能。

## 优化原则

### 问题识别

读取扩散通常出现在以下模式中：

```python
# ❌ 不好的模式 - 读取扩散
with store.transaction():
    rows = store.rows("SELECT * FROM large_table")  # 在事务开始时读取
    for row in rows:
        store.insert(...)  # 基于读取结果进行写入
```

SQLite 的行为：
1. 事务开始时，任何读取的页面都会被记录到回滚日志
2. 即使这些数据不会被修改，回滚日志仍会增长
3. 大量读取 → 大量回滚日志 → 性能下降

### 优化策略

将纯读取操作移出事务，只在真正需要写入时才开启事务：

```python
# ✅ 好的模式 - 避免读取扩散
rows = store.rows("SELECT * FROM large_table")  # 在事务外读取
with store.transaction():
    for row in rows:
        store.insert(...)  # 事务内只进行写入
```

## 已完成的优化

### 1. interest.py - recalculate_all()

**问题**：在事务开始时读取所有兴趣记录（可能数千条），导致大量页面进入回滚日志。

**优化前**：
```python
with s.transaction():
    all_records = s.rows("SELECT * FROM interest_dimensions ...")
    for record in all_records:
        decayed = decay_factor(...)
        s.execute("UPDATE interest_dimensions SET score=? ...")
```

**优化后**：
```python
all_records = s.rows("SELECT * FROM interest_dimensions ...")
with s.transaction():
    for record in all_records:
        decayed = decay_factor(...)
        s.execute("UPDATE interest_dimensions SET score=? ...")
```

**影响**：
- 减少事务内的读取页面数量
- 降低回滚日志大小
- 提升批量更新性能

### 2. goals.py - record_exploration()

**问题**：在事务开始时读取待更新的目标记录，导致不必要的页面锁定。

**优化前**：
```python
with s.transaction():
    target = s.one("SELECT * FROM goals WHERE goal_id=?", (goal_id,))
    if target and target["state"] == "ACTIVE":
        s.execute("UPDATE goals SET exploration_requests_json=? ...")
```

**优化后**：
```python
target = s.one("SELECT * FROM goals WHERE goal_id=?", (goal_id,))
if target and target["state"] == "ACTIVE":
    with s.transaction():
        s.execute("UPDATE goals SET exploration_requests_json=? ...")
```

**影响**：
- 事务更小更快
- 减少锁持有时间
- 提高并发性能

## 验证方法

### 单元测试
所有相关的单元测试都已通过，确保行为正确性：
- `tests/test_interest.py` - 兴趣系统测试
- `tests/test_goals.py` - 目标系统测试

### 性能测试
可以通过以下方式验证优化效果：

```bash
# 运行性能测试
python -m pytest tests/test_interest.py -v -k recalculate

# 监控 SQLite 行为（如果需要）
# 使用 PRAGMA compile_options 检查 WAL 模式
# 使用 EXPLAIN QUERY PLAN 分析查询
```

## 最佳实践

### 事务使用指南

1. **读取优先外移**：尽可能在事务外进行读取操作
2. **最小化事务范围**：事务内只包含必要的写入操作
3. **避免长时间持有事务**：不要在事务内执行耗时操作（如 I/O、网络调用）
4. **使用 nullcontext 避免嵌套**：当可能已在事务中时，使用 `nullcontext()` 避免嵌套

### 代码审查检查点

在代码审查时，注意以下模式：

```python
# ⚠️ 需要审查
with store.transaction():
    rows = store.rows(...)  # 大量读取
    for row in rows:
        store.insert/update/delete(...)  # 多次写入
```

问问自己：
1. 这个读取是否可以移到事务外？
2. 读取的数据在事务内是否会被其他操作修改？
3. 如果不会被修改，就应该移到事务外

## 未来优化机会

### 潜在候选模块

经过代码审查，以下模块值得进一步关注：
- `memory.py` - 某些批量内存操作
- `affect.py` - 状态更新循环
- `agent_persistence.py` - 批量持久化操作

这些模块目前使用了合理的模式，但在数据量增长时可能需要进一步优化。

### 监控建议

建议监控以下指标：
1. 事务持续时间
2. 回滚日志大小（WAL 文件大小）
3. 数据库锁等待时间
4. 批量操作的吞吐量

## 参考资料

- [SQLite Transaction](https://www.sqlite.org/lang_transaction.html)
- [SQLite WAL Mode](https://www.sqlite.org/wal.html)
- [SQLite Performance Tuning](https://www.sqlite.org/speed.html)

## 变更历史

- 2025-01: 初始优化 - interest.py 和 goals.py 的读取扩散修复
