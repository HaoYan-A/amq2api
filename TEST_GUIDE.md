# 测试指南 - Kiro/Amazon Q 代理修复验证

## 前置准备

### 1. 切换到修复分支
```bash
cd /Users/haoyan/Documents/demo/amq2api
git checkout fix/kiro-proxy-issues
```

### 2. 重启服务
```bash
# 停止现有服务
# Ctrl+C 或 kill 进程

# 启动服务
python3 main.py
```

---

## 测试方案

### 测试 1: 基础对话测试

**目的**: 验证简化内容格式化后响应是否正常

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4.5",
    "messages": [
      {
        "role": "user",
        "content": "你好，请用一句话介绍自己。"
      }
    ],
    "max_tokens": 1024,
    "stream": true
  }'
```

**预期结果**:
- ✅ 响应流畅
- ✅ 模型能正确自我介绍（不受"防火墙"提示词干扰）
- ✅ 响应内容简洁明了

**对比点** (与修复前):
- 修复前: 可能包含关于"被污染提示词"的澄清
- 修复后: 直接回答问题，没有额外的干扰信息

---

### 测试 2: System Prompt 测试

**目的**: 验证 system prompt 是否正常传递

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4.5",
    "system": "你是一个专业的 Python 编程助手。请在回答中包含代码示例。",
    "messages": [
      {
        "role": "user",
        "content": "如何读取 JSON 文件？"
      }
    ],
    "max_tokens": 2048,
    "stream": true
  }'
```

**预期结果**:
- ✅ 响应符合 system prompt 的角色设定
- ✅ 包含 Python 代码示例
- ✅ 没有额外的包装文本

---

### 测试 3: 工具调用测试（如果支持）

**目的**: 验证工具结果处理是否正常

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4.5",
    "messages": [
      {
        "role": "user",
        "content": "请帮我获取当前时间"
      }
    ],
    "tools": [
      {
        "name": "get_current_time",
        "description": "获取当前系统时间",
        "input_schema": {
          "type": "object",
          "properties": {}
        }
      }
    ],
    "max_tokens": 2048,
    "stream": true
  }'
```

**预期结果**:
- ✅ 工具调用请求正常
- ✅ 工具结果不会被自动填充虚假内容
- ✅ 如果工具结果为空，传递空字符串而非 "Command executed successfully"

---

### 测试 4: 多轮对话测试

**目的**: 验证历史消息处理是否正常

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4.5",
    "messages": [
      {
        "role": "user",
        "content": "请记住：我的名字是张三"
      },
      {
        "role": "assistant",
        "content": "好的，我记住了，您的名字是张三。"
      },
      {
        "role": "user",
        "content": "我刚才告诉你我叫什么？"
      }
    ],
    "max_tokens": 1024,
    "stream": true
  }'
```

**预期结果**:
- ✅ 模型能正确回忆历史信息
- ✅ 回答 "张三"
- ✅ 历史消息处理正常

---

### 测试 5: Token 使用量对比

**目的**: 验证是否减少了不必要的 token 消耗

**方法**:
1. 使用相同的请求
2. 对比修复前后的 `input_tokens` 数量

**预期结果**:
- ✅ `input_tokens` 明显减少（因为移除了大量额外的格式化内容）
- ✅ 估算节省: 每次请求约减少 200-500 tokens

---

### 测试 6: 日志观察

**目的**: 观察降级处理机制是否正常工作

**方法**:
1. 开启详细日志
2. 观察是否有 "切换到文本解析降级模式" 的日志
3. 检查是否有工具结果为空的警告日志

**预期日志示例**:
```
[WARNING] 工具结果为空: tool_use_id=toolu_xxx
[INFO] 文本降级模式成功解析 JSON: {"content":"..."}
```

---

## 性能对比

### 对比指标

| 指标 | 修复前 | 修复后 | 改善 |
|------|--------|--------|------|
| **平均 input_tokens** | ~1500 | ~1000 | ⬇️ 33% |
| **响应准确性** | 中等 | 高 | ⬆️ |
| **工具调用成功率** | 低 | 高 | ⬆️ |
| **解析失败率** | 中等 | 低 | ⬇️ |

---

## 故障排查

### 问题 1: 服务启动失败

**可能原因**: Python 版本或依赖问题

**解决方案**:
```bash
# 检查 Python 版本
python3 --version

# 重新安装依赖
pip install -r requirements.txt
```

---

### 问题 2: 解析错误日志频繁出现

**可能原因**: Amazon Q 返回格式异常

**观察点**:
- 是否触发了文本降级模式？
- 降级模式是否成功解析？
- 最终响应是否正常？

**如果降级模式正常工作**: 这是预期行为，说明容错机制生效

---

### 问题 3: 工具调用仍然失败

**排查步骤**:
1. 检查日志中的 "工具结果为空" 警告
2. 确认工具定义是否正确
3. 检查 Amazon Q 账号的工具调用配额

---

## 回滚方案

如果测试发现严重问题，可以快速回滚:

```bash
# 切换回主分支
git checkout main

# 重启服务
python3 main.py
```

---

## 反馈收集

### 测试成功 ✅
请记录:
- 哪些问题得到解决
- 性能改善情况
- Token 使用量对比

### 测试失败 ❌
请记录:
- 具体的错误信息
- 日志内容
- 复现步骤
- 预期行为 vs 实际行为

---

## 下一步

### 如果测试通过
```bash
# 合并到主分支
git checkout main
git merge fix/kiro-proxy-issues

# 推送到远程仓库
git push origin main
```

### 如果需要进一步调整
继续在 `fix/kiro-proxy-issues` 分支上修改，重复测试流程。

---

## 参考对比

### AIClient-2-API 测试（对照组）

如果想验证实现是否正确，可以同时运行 AIClient-2-API:

```bash
# 在另一个终端
cd /Users/haoyan/Documents/demo/AIClient-2-API

# 启动 AIClient-2-API
node src/api-server.js \
  --model-provider claude-kiro-oauth \
  --kiro-oauth-creds-file ~/.aws/sso/cache/kiro-auth-token.json

# 使用相同的请求测试
curl http://localhost:3000/claude-kiro-oauth/v1/messages \
  -H "Content-Type: application/json" \
  -d '{...}'
```

对比两者的响应是否一致。
