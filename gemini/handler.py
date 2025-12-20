"""
Gemini 流式响应处理器
将 Gemini SSE 响应转换为 Claude SSE 格式
"""
import json
import logging
from typing import AsyncIterator

logger = logging.getLogger(__name__)


async def handle_gemini_stream(response_stream: AsyncIterator[bytes], model: str) -> AsyncIterator[str]:
    """
    处理 Gemini SSE 流式响应，转换为 Claude SSE 格式

    Args:
        response_stream: Gemini 响应流
        model: 模型名称

    Yields:
        Claude 格式的 SSE 事件
    """
    # 跟踪内容块和 token 统计
    content_blocks = []
    current_index = -1
    input_tokens = 0
    output_tokens = 0
    content_block_started = False
    content_block_stop_sent = False
    message_id = "msg_gemini"  # 默认 ID，会从响应中更新
    message_start_sent = False  # 标记是否已发送 message_start

    # 处理流式响应
    buffer = ""
    byte_buffer = b""  # 用于累积不完整的 UTF-8 字节

    chunk_count = 0
    async for chunk in response_stream:
        chunk_count += 1
        if not chunk:
            logger.debug(f"[Chunk {chunk_count}] 收到空 chunk")
            continue

        logger.debug(f"[Chunk {chunk_count}] 收到 {len(chunk)} 字节")

        try:
            # 累积字节
            byte_buffer += chunk

            # 尝试解码,使用 'ignore' 忽略不完整的字节序列
            try:
                text = byte_buffer.decode('utf-8')
                byte_buffer = b""  # 解码成功,清空字节缓冲区
                logger.debug(f"[Chunk {chunk_count}] 解码成功: {text[:200]}")
            except UnicodeDecodeError as e:
                # 解码失败,可能是不完整的多字节字符,等待更多数据
                logger.debug(f"[Chunk {chunk_count}] 解码失败: {e}, byte_buffer 长度: {len(byte_buffer)}")
                # 保留最后几个字节(最多4个,UTF-8最多4字节)
                if len(byte_buffer) > 4:
                    # 尝试解码前面的部分
                    text = byte_buffer[:-4].decode('utf-8', errors='ignore')
                    byte_buffer = byte_buffer[-4:]
                    logger.debug(f"[Chunk {chunk_count}] 部分解码: {text[:200]}")
                else:
                    # 字节太少,继续等待
                    logger.debug(f"[Chunk {chunk_count}] 字节太少,等待更多数据")
                    continue

            buffer += text

            while '\r\n\r\n' in buffer:
                event_text, buffer = buffer.split('\r\n\r\n', 1)
                logger.debug(f"[事件解析] event_text: {event_text[:300]}")

                if event_text.startswith('data: '):
                    data_str = event_text[6:]
                    if data_str.strip() == '[DONE]':
                        logger.info("[事件] 收到 [DONE] 标记")
                        continue

                    try:
                        data = json.loads(data_str)
                        response_data = data.get('response', data)
                        logger.info(f"[事件] 收到响应: {json.dumps(response_data, ensure_ascii=False)[:500]}")

                        # 提取 responseId 并发送 message_start（仅第一次）
                        if not message_start_sent and 'responseId' in response_data:
                            message_id = response_data['responseId']
                            yield format_sse_event("message_start", {
                                "type": "message_start",
                                "message": {
                                    "id": message_id,
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [],
                                    "model": model,
                                    "stop_reason": None,
                                    "stop_sequence": None,
                                    "usage": {"input_tokens": 0, "output_tokens": 0}
                                }
                            })
                            message_start_sent = True

                        # 提取 usageMetadata (如果存在)
                        if 'usageMetadata' in response_data:
                            usage_meta = response_data['usageMetadata']
                            input_tokens = usage_meta.get('promptTokenCount', 0)
                            output_tokens = usage_meta.get('candidatesTokenCount', 0)
                            logger.info(f"[Token统计] input={input_tokens}, output={output_tokens}")

                        if 'candidates' in response_data:
                            for candidate in response_data['candidates']:
                                content = candidate.get('content', {})
                                parts = content.get('parts', [])

                                for part in parts:
                                    # 处理 thinking 内容
                                    if part.get('thought'):
                                        if 'text' in part and part['text']:
                                            # 开启 thinking 块
                                            if not content_block_started:
                                                current_index += 1
                                                content_blocks.append({'type': 'thinking'})
                                                yield format_sse_event("content_block_start", {
                                                    "type": "content_block_start",
                                                    "index": current_index,
                                                    "content_block": {"type": "thinking", 'thinking': ""}
                                                })
                                                content_block_started = True
                                                content_block_stop_sent = False

                                            # 发送 thinking delta
                                            yield format_sse_event("content_block_delta", {
                                                "type": "content_block_delta",
                                                "index": current_index,
                                                "delta": {"type": "thinking_delta", "thinking": part['text']}
                                            })

                                        # thinking 结束标记
                                        if 'thoughtSignature' in part:
                                            if content_block_started and not content_block_stop_sent:
                                                # 先发送 signature_delta
                                                yield format_sse_event("content_block_delta", {
                                                    "type": "content_block_delta",
                                                    "index": current_index,
                                                    "delta": {"type": "signature_delta", "signature": part['thoughtSignature']}
                                                })
                                                # 再发送 content_block_stop
                                                yield format_sse_event("content_block_stop", {
                                                    "type": "content_block_stop",
                                                    "index": current_index
                                                })
                                                content_block_stop_sent = True
                                                content_block_started = False
                                    # 处理文本内容
                                    elif 'text' in part and part['text']:
                                        if not content_block_started or (current_index >= 0 and content_blocks[current_index]['type'] != 'text'):
                                            current_index += 1
                                            content_blocks.append({'type': 'text'})
                                            yield format_sse_event("content_block_start", {
                                                "type": "content_block_start",
                                                "index": current_index,
                                                "content_block": {"type": "text", "text": ""}
                                            })
                                            content_block_started = True
                                            content_block_stop_sent = False

                                        yield format_sse_event("content_block_delta", {
                                            "type": "content_block_delta",
                                            "index": current_index,
                                            "delta": {"type": "text_delta", "text": part['text']}
                                        })

                                    # 处理工具调用
                                    elif 'functionCall' in part:
                                        func_call = part['functionCall']
                                        current_index += 1
                                        content_blocks.append({'type': 'tool_use'})

                                        yield format_sse_event("content_block_start", {
                                            "type": "content_block_start",
                                            "index": current_index,
                                            "content_block": {
                                                "type": "tool_use",
                                                "id": func_call.get('id', f"toolu_{current_index}"),
                                                "name": func_call['name'],
                                                "input": {}
                                            }
                                        })

                                        yield format_sse_event("content_block_delta", {
                                            "type": "content_block_delta",
                                            "index": current_index,
                                            "delta": {
                                                "type": "input_json_delta",
                                                "partial_json": json.dumps(func_call.get('args', {}))
                                            }
                                        })

                                        yield format_sse_event("content_block_stop", {
                                            "type": "content_block_stop",
                                            "index": current_index
                                        })

                    except json.JSONDecodeError as e:
                        logger.warning(f"[JSON错误] 解析失败: {e}, data: {data_str[:200]}")
                        continue

        except Exception as e:
            logger.error(f"[异常] 处理流式响应时出错: {e}", exc_info=True)
            continue

    logger.info(f"[流结束] 共处理 {chunk_count} 个 chunk, 最终 buffer 长度: {len(buffer)}")

    # 处理 buffer 中剩余的数据
    if buffer.strip():
        if buffer.startswith('data: '):
            data_str = buffer[6:]
            if data_str.strip() and data_str.strip() != '[DONE]':
                try:
                    data = json.loads(data_str)
                    response_data = data.get('response', data)

                    if 'candidates' in response_data:
                        for candidate in response_data['candidates']:
                            content = candidate.get('content', {})
                            parts = content.get('parts', [])

                            for part in parts:
                                if 'text' in part and part['text']:
                                    if current_index == -1 or content_blocks[current_index]['type'] != 'text':
                                        current_index += 1
                                        content_blocks.append({'type': 'text'})
                                        yield format_sse_event("content_block_start", {
                                            "type": "content_block_start",
                                            "index": current_index,
                                            "content_block": {"type": "text", "text": ""}
                                        })

                                    yield format_sse_event("content_block_delta", {
                                        "type": "content_block_delta",
                                        "index": current_index,
                                        "delta": {"type": "text_delta", "text": part['text']}
                                    })
                except json.JSONDecodeError:
                    pass

    # 关闭最后一个文本块
    if current_index >= 0 and content_blocks[current_index]['type'] == 'text':
        logger.info(f"[结束] 关闭最后一个文本块 index={current_index}")
        yield format_sse_event("content_block_stop", {
            "type": "content_block_stop",
            "index": current_index
        })

    # 发送 message_delta 事件
    logger.info(f"[结束] 发送 message_delta: input_tokens={input_tokens}, output_tokens={output_tokens}")
    yield format_sse_event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}
    })

    # 发送 message_stop 事件
    logger.info("[结束] 发送 message_stop")
    yield format_sse_event("message_stop", {
        "type": "message_stop"
    })


def format_sse_event(event_type: str, data: dict) -> str:
    """
    格式化 SSE 事件

    Args:
        event_type: 事件类型
        data: 事件数据

    Returns:
        格式化的 SSE 事件字符串
    """
    result = f"event: {event_type}\r\ndata: {json.dumps(data, ensure_ascii=False)}\r\n\r\n"
    return result