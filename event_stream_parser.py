"""
AWS Event Stream 解析器
解析 Amazon Q 返回的 vnd.amazon.eventstream 格式数据
"""
import struct
import json
import logging
from typing import Optional, Dict, Any, AsyncIterator
from io import BytesIO

logger = logging.getLogger(__name__)


class EventStreamParser:
    """
    AWS Event Stream 解析器

    Event Stream 格式：
    - Prelude (12 bytes):
      - Total length (4 bytes, big-endian uint32)
      - Headers length (4 bytes, big-endian uint32)
      - Prelude CRC (4 bytes, big-endian uint32)
    - Headers (variable length)
    - Payload (variable length)
    - Message CRC (4 bytes, big-endian uint32)
    """

    @staticmethod
    def parse_headers(headers_data: bytes) -> Dict[str, str]:
        """
        解析事件头部

        头部格式：
        - Header name length (1 byte)
        - Header name (variable)
        - Header value type (1 byte, 7=string)
        - Header value length (2 bytes, big-endian uint16)
        - Header value (variable)
        """
        headers = {}
        offset = 0

        while offset < len(headers_data):
            # 读取头部名称长度
            if offset >= len(headers_data):
                break
            name_length = headers_data[offset]
            offset += 1

            # 读取头部名称
            if offset + name_length > len(headers_data):
                break
            name = headers_data[offset:offset + name_length].decode('utf-8')
            offset += name_length

            # 读取值类型
            if offset >= len(headers_data):
                break
            value_type = headers_data[offset]
            offset += 1

            # 读取值长度（2 字节）
            if offset + 2 > len(headers_data):
                break
            value_length = struct.unpack('>H', headers_data[offset:offset + 2])[0]
            offset += 2

            # 读取值
            if offset + value_length > len(headers_data):
                break

            if value_type == 7:  # String type
                value = headers_data[offset:offset + value_length].decode('utf-8')
            else:
                value = headers_data[offset:offset + value_length]

            offset += value_length
            headers[name] = value

        return headers

    @staticmethod
    def parse_message(data: bytes) -> Optional[Dict[str, Any]]:
        """
        解析单个 Event Stream 消息

        Args:
            data: 完整的消息字节数据

        Returns:
            Optional[Dict[str, Any]]: 解析后的消息，包含 headers 和 payload
        """
        try:
            if len(data) < 16:  # 最小消息长度
                return None

            # 解析 Prelude (12 bytes)
            total_length = struct.unpack('>I', data[0:4])[0]
            headers_length = struct.unpack('>I', data[4:8])[0]
            # prelude_crc = struct.unpack('>I', data[8:12])[0]

            # 验证长度
            if len(data) < total_length:
                logger.warning(f"消息不完整: 期望 {total_length} 字节，实际 {len(data)} 字节")
                return None

            # 解析头部
            headers_data = data[12:12 + headers_length]
            headers = EventStreamParser.parse_headers(headers_data)

            # 解析 Payload
            payload_start = 12 + headers_length
            payload_end = total_length - 4  # 减去最后的 CRC
            payload_data = data[payload_start:payload_end]

            # 尝试解析 JSON payload
            payload = None
            if payload_data:
                try:
                    payload = json.loads(payload_data.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    payload = payload_data

            return {
                'headers': headers,
                'payload': payload,
                'total_length': total_length
            }

        except Exception as e:
            logger.error(f"解析消息失败: {e}", exc_info=True)
            return None

    @staticmethod
    async def parse_stream(byte_stream: AsyncIterator[bytes]) -> AsyncIterator[Dict[str, Any]]:
        """
        解析字节流，提取事件
        支持二进制格式和文本格式的降级处理（参考 AIClient-2-API 的容错设计）

        Args:
            byte_stream: 异步字节流

        Yields:
            Dict[str, Any]: 解析后的事件
        """
        buffer = bytearray()
        parse_errors = 0  # 记录解析错误次数
        max_errors = 3    # 连续错误超过此值时切换到文本解析模式

        async for chunk in byte_stream:
            buffer.extend(chunk)

            # 尝试解析缓冲区中的消息
            while len(buffer) >= 12:
                # 读取消息总长度
                try:
                    total_length = struct.unpack('>I', buffer[0:4])[0]
                except struct.error:
                    parse_errors += 1
                    logger.warning(f"无法读取消息长度，解析错误计数: {parse_errors}")

                    # 如果错误过多，尝试文本解析模式
                    if parse_errors >= max_errors:
                        logger.warning("切换到文本解析降级模式")
                        # 尝试在缓冲区中搜索 JSON payload
                        for event in EventStreamParser._parse_text_fallback(buffer):
                            yield event
                        buffer.clear()
                        parse_errors = 0
                    break

                # 检查是否有完整的消息
                if len(buffer) < total_length:
                    break

                # 提取完整消息
                message_data = bytes(buffer[:total_length])
                buffer = buffer[total_length:]

                # 解析消息
                try:
                    message = EventStreamParser.parse_message(message_data)
                    if message:
                        parse_errors = 0  # 重置错误计数
                        yield message
                    else:
                        parse_errors += 1
                        logger.warning(f"消息解析失败，错误计数: {parse_errors}")
                except Exception as e:
                    parse_errors += 1
                    logger.error(f"解析消息异常: {e}，错误计数: {parse_errors}")

                    # 如果错误过多，切换到文本解析
                    if parse_errors >= max_errors:
                        logger.warning("切换到文本解析降级模式")
                        for event in EventStreamParser._parse_text_fallback(buffer):
                            yield event
                        buffer.clear()
                        parse_errors = 0

    @staticmethod
    def _parse_text_fallback(buffer: bytearray) -> list[Dict[str, Any]]:
        """
        文本解析降级方法（参考 AIClient-2-API 的实现）
        在二进制解析失败时，直接在字节流中搜索 JSON payload

        Args:
            buffer: 字节缓冲区

        Returns:
            list[Dict[str, Any]]: 解析出的事件列表
        """
        events = []
        try:
            # 尝试将缓冲区转为文本
            text = buffer.decode('utf-8', errors='ignore')

            # 搜索可能的 JSON payload 模式
            # Amazon Q 返回格式: {"content":"..."} 或 {"name":"...","toolUseId":"..."}
            search_start = 0
            while True:
                # 查找所有可能的 JSON 开头
                content_start = text.find('{"content":', search_start)
                name_start = text.find('{"name":', search_start)
                input_start = text.find('{"input":', search_start)
                stop_start = text.find('{"stop":', search_start)

                # 找到最早出现的 JSON
                candidates = [pos for pos in [content_start, name_start, input_start, stop_start] if pos >= 0]
                if not candidates:
                    break

                json_start = min(candidates)

                # 使用括号计数法找到 JSON 结束位置
                brace_count = 0
                in_string = False
                escape_next = False
                json_end = -1

                for i in range(json_start, len(text)):
                    char = text[i]

                    if escape_next:
                        escape_next = False
                        continue

                    if char == '\\':
                        escape_next = True
                        continue

                    if char == '"' and not escape_next:
                        in_string = not in_string
                        continue

                    if not in_string:
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                json_end = i
                                break

                if json_end < 0:
                    break

                # 提取 JSON 字符串并解析
                json_str = text[json_start:json_end + 1]
                try:
                    payload = json.loads(json_str)

                    # 构造事件消息（模拟正常的事件流格式）
                    if 'content' in payload:
                        events.append({
                            'headers': {':event-type': 'assistantResponseEvent'},
                            'payload': payload
                        })
                    elif 'name' in payload and 'toolUseId' in payload:
                        events.append({
                            'headers': {':event-type': 'toolUseEvent'},
                            'payload': payload
                        })
                    elif 'input' in payload:
                        events.append({
                            'headers': {':event-type': 'toolUseEvent'},
                            'payload': payload
                        })
                    elif 'stop' in payload:
                        events.append({
                            'headers': {':event-type': 'toolUseEvent'},
                            'payload': payload
                        })

                    logger.info(f"文本降级模式成功解析 JSON: {json_str[:100]}")
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON 解析失败: {e}")

                search_start = json_end + 1

        except Exception as e:
            logger.error(f"文本降级解析失败: {e}")

        return events


def extract_event_info(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    从解析后的消息中提取事件信息

    Args:
        message: 解析后的消息

    Returns:
        Optional[Dict[str, Any]]: 事件信息
    """
    headers = message.get('headers', {})
    payload = message.get('payload')

    event_type = headers.get(':event-type') or headers.get('event-type')
    content_type = headers.get(':content-type') or headers.get('content-type')
    message_type = headers.get(':message-type') or headers.get('message-type')

    return {
        'event_type': event_type,
        'content_type': content_type,
        'message_type': message_type,
        'payload': payload
    }


# 简化的文本解析器（备用方案）
def parse_text_stream_line(line: str) -> Optional[Dict[str, Any]]:
    """
    解析文本格式的事件流（备用方案）

    从您提供的数据看，可以尝试提取可读部分：
    :event-type assistantResponseEvent
    :content-type application/json
    :message-type event
    {"content":"..."}
    """
    line = line.strip()

    # 跳过空行
    if not line:
        return None

    # 尝试解析 JSON
    if line.startswith('{') and line.endswith('}'):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            pass

    return None
