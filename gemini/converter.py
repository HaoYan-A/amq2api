"""
Gemini 请求格式转换器
将 Claude API 格式转换为 Gemini API 格式
"""
import logging
import uuid
import random
from typing import Dict, Any, List
from models import ClaudeRequest

logger = logging.getLogger(__name__)


def convert_claude_to_gemini(claude_req: ClaudeRequest, project: str) -> Dict[str, Any]:
    """
    将 Claude API 请求转换为 Gemini API 请求格式

    Args:
        claude_req: Claude 请求对象
        project: Gemini 项目 ID

    Returns:
        Gemini 请求字典
    """
    # 转换消息格式
    contents = []
    for msg in claude_req.messages:
        role = "user" if msg.role == "user" else "model"

        # 处理 content
        if isinstance(msg.content, str):
            parts = [{"text": msg.content}]
        elif isinstance(msg.content, list):
            parts = []
            for item in msg.content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append({"text": item.get("text", "")})
                    elif item.get("type") == "image":
                        # 处理图片
                        source = item.get("source", {})
                        if source.get("type") == "base64":
                            parts.append({
                                "inlineData": {
                                    "mimeType": source.get("media_type", "image/png"),
                                    "data": source.get("data", "")
                                }
                            })
                    elif item.get("type") == "tool_use":
                        # 处理工具调用
                        parts.append({
                            "functionCall": {
                                "id": item.get("id"),
                                "name": item.get("name"),
                                "args": item.get("input", {})
                            }
                        })
                    elif item.get("type") == "tool_result":
                        # 处理工具结果
                        content = item.get("content", "")
                        if isinstance(content, list):
                            content = content[0].get("text", "") if content else ""
                        parts.append({
                            "functionResponse": {
                                "id": item.get("tool_use_id"),
                                "name": item.get("name", ""),
                                "response": {"output": content}
                            }
                        })
                else:
                    parts.append({"text": str(item)})
        else:
            parts = [{"text": str(msg.content)}]

        contents.append({
            "role": role,
            "parts": parts
        })

    # 重新组织消息，确保 tool_use 后紧跟对应的 tool_result
    contents = reorganize_tool_messages(contents)

    # 构建 Gemini 请求
    gemini_request = {
        "project": project,
        "requestId": f"agent-{uuid.uuid4()}",
        "request": {
            "contents": contents,
            "generationConfig": {
                "temperature": claude_req.temperature if claude_req.temperature is not None else 0.4,
                "topP": 1,
                "topK": 40,
                "candidateCount": 1,
                "maxOutputTokens": claude_req.max_tokens,
                "stopSequences": ["<|user|>", "<|bot|>", "<|context_request|>", "<|endoftext|>", "<|end_of_turn|>"]
            },
            "sessionId": "-3750763034362895578",
        },
        "model": map_claude_model_to_gemini(claude_req.model),
        "userAgent": "antigravity/1.11.3 darwin/arm64",
        "requestType": "agent"
    }

    # 添加 system instruction
    if claude_req.system:
        # 处理 system 字段（可能是字符串或列表）
        if isinstance(claude_req.system, str):
            # 简单字符串格式
            system_parts = [{"text": claude_req.system}]
        elif isinstance(claude_req.system, list):
            # 列表格式，提取所有 text 内容
            system_parts = []
            for item in claude_req.system:
                if isinstance(item, dict) and item.get("type") == "text":
                    system_parts.append({"text": item.get("text", "")})
        else:
            system_parts = [{"text": str(claude_req.system)}]

        gemini_request["request"]["systemInstruction"] = {
            "role": "user",
            "parts": system_parts
        }

    # 添加工具
    if claude_req.tools:
        gemini_request["request"]["tools"] = convert_tools(claude_req.tools)
        gemini_request["request"]["toolConfig"] = {
            "functionCallingConfig": {
                "mode": "VALIDATED"
            }
        }

    return gemini_request


def map_claude_model_to_gemini(claude_model: str) -> str:
    """
    将 Claude 模型名称映射到 Gemini 模型名称
    如果请求的模型已经存在于支持列表中，则直接透传

    Args:
        claude_model: Claude 模型名称或 Gemini 模型名称

    Returns:
        Gemini 模型名称
    """
    # 支持的所有模型（直接透传）
    supported_models = {
        "gemini-2.5-flash", "gemini-2.5-flash-thinking", "gemini-2.5-pro",
        "gemini-3-pro-low", "gemini-3-pro-high", "gemini-2.5-flash-lite",
        "gemini-2.5-flash-image", "gemini-2.5-flash-image",
        "claude-sonnet-4-5", "claude-sonnet-4-5-thinking", "claude-opus-4-5-thinking",
        "gpt-oss-120b-medium"
    }

    if claude_model in supported_models:
        return claude_model

    # Claude 标准模型名称映射
    model_mapping = {
        "claude-sonnet-4.5": "claude-sonnet-4-5",
        "claude-3-5-sonnet-20241022": "claude-sonnet-4-5",
        "claude-3-5-sonnet-20240620": "claude-sonnet-4-5",
        "claude-opus-4": "gemini-3-pro-high",
        "claude-haiku-4": "claude-haiku-4.5",
        "claude-3-haiku-20240307": "gemini-2.5-flash"
    }

    return model_mapping.get(claude_model, "claude-sonnet-4-5")


def reorganize_tool_messages(contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    重新组织消息，确保每个 tool_use 后紧跟对应的 tool_result

    Gemini 要求：每个 functionCall 后必须紧跟对应的 functionResponse
    Claude Code 可能发送：
    1. 一条 model 消息，parts 包含多个 functionCall
    2. 下一条 user 消息，parts 包含多个 functionResponse

    需要重新组织为：
    1. model 消息，parts 包含 functionCall
    2. user 消息，parts 包含对应的 functionResponse
    3. model 消息，parts 包含下一个 functionCall
    4. user 消息，parts 包含对应的 functionResponse
    ...

    Args:
        contents: 原始消息列表

    Returns:
        重新组织后的消息列表
    """
    # 收集所有 tool_use 和 tool_result
    tool_uses = {}  # {tool_id: (msg_index, part_index, tool_use_data)}
    tool_results = {}  # {tool_id: (msg_index, part_index, tool_result_data)}

    for msg_idx, msg in enumerate(contents):
        for part_idx, part in enumerate(msg.get("parts", [])):
            if "functionCall" in part:
                tool_id = part["functionCall"].get("id")
                if tool_id:
                    tool_uses[tool_id] = (msg_idx, part_idx, part)
            elif "functionResponse" in part:
                tool_id = part["functionResponse"].get("id")
                if tool_id:
                    tool_results[tool_id] = (msg_idx, part_idx, part)

    # 找出配对的 tool_use 和 tool_result
    paired_tool_ids = set(tool_uses.keys()) & set(tool_results.keys())

    # 如果没有工具调用，直接返回
    if not paired_tool_ids:
        logger.info("没有找到需要重新组织的工具调用")
        return contents

    logger.info(f"找到 {len(paired_tool_ids)} 对配对的工具调用")

    # 重新构建消息列表
    new_contents = []
    processed_indices = set()  # 记录已处理的消息索引

    for msg_idx, msg in enumerate(contents):
        # 检查这条消息是否包含 tool_use
        msg_tool_uses = []
        other_parts = []

        for part in msg.get("parts", []):
            if "functionCall" in part:
                tool_id = part["functionCall"].get("id")
                if tool_id in paired_tool_ids:
                    msg_tool_uses.append((tool_id, part))
                else:
                    # 没有配对的 tool_use，清理掉
                    logger.warning(f"清理没有配对的 tool_use: {tool_id}")
            elif "functionResponse" in part:
                tool_id = part["functionResponse"].get("id")
                if tool_id not in paired_tool_ids:
                    # 没有配对的 tool_result，清理掉
                    logger.warning(f"清理没有配对的 tool_result: {tool_id}")
            else:
                other_parts.append(part)

        # 如果这条消息包含 tool_use
        if msg_tool_uses:
            # 先添加非工具调用的部分（如果有）
            if other_parts:
                new_contents.append({
                    "role": msg["role"],
                    "parts": other_parts
                })

            # 为每个 tool_use 创建一对消息
            for tool_id, tool_use_part in msg_tool_uses:
                # 添加 tool_use 消息
                new_contents.append({
                    "role": "model",
                    "parts": [tool_use_part]
                })

                # 添加对应的 tool_result 消息
                if tool_id in tool_results:
                    _, _, tool_result_part = tool_results[tool_id]
                    new_contents.append({
                        "role": "user",
                        "parts": [tool_result_part]
                    })

            processed_indices.add(msg_idx)
        elif any("functionResponse" in part for part in msg.get("parts", [])):
            # 这条消息只包含 tool_result，已经在上面处理过了
            processed_indices.add(msg_idx)
        else:
            # 普通消息，直接添加
            if msg_idx not in processed_indices:
                new_contents.append(msg)
                processed_indices.add(msg_idx)

    logger.info(f"重新组织后的消息数量: {len(new_contents)}")
    return new_contents


def convert_tools(claude_tools: List[Any]) -> List[Dict[str, Any]]:
    """
    将 Claude 工具格式转换为 Gemini 工具格式

    Args:
        claude_tools: Claude 工具列表

    Returns:
        Gemini 工具列表
    """
    gemini_tools = []

    for tool in claude_tools:
        # 清理 JSON Schema，移除 Gemini 不支持的字段
        parameters = clean_json_schema(tool.input_schema)

        gemini_tool = {
            "functionDeclarations": [{
                "name": tool.name,
                "description": tool.description,
                "parameters": parameters
            }]
        }
        gemini_tools.append(gemini_tool)

    return gemini_tools


def clean_json_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    清理 JSON Schema，移除 Gemini 不支持的字段，并将验证要求追加到 description

    Args:
        schema: 原始 JSON Schema

    Returns:
        清理后的 JSON Schema
    """
    if not isinstance(schema, dict):
        return schema

    # 需要移除的验证字段
    validation_fields = {
        "minLength": "minLength",
        "maxLength": "maxLength",
        "minimum": "minimum",
        "maximum": "maximum",
        "minItems": "minItems",
        "maxItems": "maxItems",
    }

    # 需要完全移除的字段
    fields_to_remove = {"$schema", "additionalProperties"}

    # 收集验证信息
    validations = []
    for field, label in validation_fields.items():
        if field in schema:
            validations.append(f"{label}: {schema[field]}")

    # 递归清理 schema
    cleaned = {}
    for key, value in schema.items():
        if key in fields_to_remove or key in validation_fields:
            continue

        if key == "description" and validations:
            # 将验证要求追加到 description
            cleaned[key] = f"{value} ({', '.join(validations)})"
        elif isinstance(value, dict):
            cleaned[key] = clean_json_schema(value)
        elif isinstance(value, list):
            cleaned[key] = [clean_json_schema(item) if isinstance(item, dict) else item for item in value]
        else:
            cleaned[key] = value

    # 如果有验证信息但没有 description 字段，添加一个
    if validations and "description" not in cleaned:
        cleaned["description"] = f"Validation: {', '.join(validations)}"

    return cleaned