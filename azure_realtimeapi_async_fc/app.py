import os
import asyncio
import base64
import json
from uuid import uuid4
from typing import Optional, Any

import chainlit as cl
from chainlit.logger import logger
from dotenv import load_dotenv

from openai import AsyncAzureOpenAI
from openai.resources.beta.realtime.realtime import (
    AsyncRealtimeConnectionManager,
    AsyncRealtimeConnection,
)

# ==============================================================================
# 設定
# ==============================================================================
load_dotenv()

# Azure OpenAI設定
AZURE_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
AZURE_DEPLOYMENT: str = os.environ.get("AZURE_OPENAI_DEPLOYMENT") or ""

if not AZURE_DEPLOYMENT:
    raise RuntimeError(
        "AZURE_OPENAI_DEPLOYMENT が未設定です。Realtimeモデルのデプロイ名(Deployment name)を設定してください。"
    )


def _create_azure_client() -> AsyncAzureOpenAI:
    """Azure OpenAI クライアントを作成"""
    return AsyncAzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=AZURE_API_VERSION,
    )


# ==============================================================================
# セッション管理用のキー
# ==============================================================================
KEY_CONN = "azure_realtime_connection"
KEY_RECV_TASK = "azure_realtime_recv_task"
KEY_CM = "azure_realtime_connection_manager"
KEY_TRACK_ID = "track_id"

# 状態管理フラグ
KEY_IS_GENERATING = "is_generating"
KEY_IS_PLAYING = "is_playing"

# メッセージストリーミング管理
KEY_CURRENT_RESPONSE_MSG = "current_response_message"
KEY_CURRENT_USER_MSG = "current_user_message"
KEY_TEXT_STREAM_LOCKED = "text_stream_locked"

# Function Call管理
KEY_PENDING_FUNCTION_CALLS = "pending_function_calls"


def _ensure_session_defaults():
    """セッションのデフォルト値を設定"""
    defaults = {
        KEY_IS_GENERATING: False,
        KEY_IS_PLAYING: False,
        KEY_TRACK_ID: str(uuid4()),
        KEY_TEXT_STREAM_LOCKED: False,
        KEY_CURRENT_RESPONSE_MSG: None,
        KEY_CURRENT_USER_MSG: None,
        KEY_PENDING_FUNCTION_CALLS: {},
    }
    
    for key, default_value in defaults.items():
        if cl.user_session.get(key) is None:
            cl.user_session.set(key, default_value)


def _get_track_id() -> str:
    """音声トラックIDを取得(存在しない場合は生成)"""
    tid = cl.user_session.get(KEY_TRACK_ID)
    if not isinstance(tid, str) or not tid:
        tid = str(uuid4())
        cl.user_session.set(KEY_TRACK_ID, tid)
    return tid


# ==============================================================================
# 天気予報ツール実装
# ==============================================================================
async def get_weather(location: str, date: Optional[str] = None) -> dict:
    """
    天気予報を取得する関数
    
    Args:
        location: 地名(例: "東京", "大阪", "Tokyo")
        date: 日付(例: "今日", "明日", "2025-10-05")
    
    Returns:
        天気予報情報を含む辞書
    """
    # 実際の天気APIを使用する場合は、ここでAPI呼び出しを行う
    # 例: OpenWeatherMap, weatherapi.com など
    
    # ダミーデータを返す(実際の実装では外部APIを呼び出す)
    await asyncio.sleep(0.5)  # API呼び出しをシミュレート
    
    date_display = date if date else "今日"
    
    # 簡単なダミーロジック
    weather_patterns = {
        "東京": {"weather": "晴れ", "temp": "25", "humidity": "60"},
        "大阪": {"weather": "曇り", "temp": "23", "humidity": "65"},
        "札幌": {"weather": "雨", "temp": "18", "humidity": "75"},
        "福岡": {"weather": "晴れ", "temp": "26", "humidity": "55"},
    }
    
    # 地名を正規化
    location_normalized = location.replace("市", "").replace("都", "").strip()
    
    # デフォルトの天気情報
    weather_info = weather_patterns.get(
        location_normalized,
        {"weather": "晴れのち曇り", "temp": "24", "humidity": "62"}
    )
    
    return {
        "location": location,
        "date": date_display,
        "weather": weather_info["weather"],
        "temperature": weather_info["temp"],
        "humidity": weather_info["humidity"],
        "forecast": f"{location}の{date_display}の天気は{weather_info['weather']}、気温は{weather_info['temp']}度、湿度は{weather_info['humidity']}%の予報です。"
    }


async def search_database(query: str, category: Optional[str] = None) -> dict:
    """
    データベースを検索する関数(長時間実行される処理のシミュレーション)
    
    非同期Function Callingの動作確認用:
    この関数は意図的に20秒かかるように設定されており、
    モデルが結果を待っている間も会話を継続できることを確認できます。
    
    Args:
        query: 検索クエリ
        category: カテゴリ(オプション)
    
    Returns:
        検索結果を含む辞書
    """
    # 長時間実行をシミュレート
    wait_time = 20
    logger.info(f"🔍 データベース検索開始: {query} (約{wait_time}秒かかります)")
    await asyncio.sleep(wait_time)
    
    # ダミーの検索結果
    results = [
        {
            "id": 1,
            "title": f"{query}に関する最新の研究論文",
            "summary": "2025年の最新データに基づく包括的な分析結果です。",
            "relevance": "95%"
        },
        {
            "id": 2,
            "title": f"{query}の実践的なガイドライン",
            "summary": "業界標準のベストプラクティスをまとめたドキュメントです。",
            "relevance": "88%"
        },
        {
            "id": 3,
            "title": f"{query}に関するケーススタディ",
            "summary": "実際の導入事例と成果について詳しく解説しています。",
            "relevance": "82%"
        }
    ]
    
    category_text = f" (カテゴリ: {category})" if category else ""
    
    return {
        "query": query,
        "category": category,
        "total_results": len(results),
        "results": results,
        "summary": f"「{query}」{category_text}に関して{len(results)}件の結果が見つかりました。検索には{wait_time}秒かかりました。"
    }


# ==============================================================================
# Function Call処理
# ==============================================================================
async def _execute_function_call(name: str, arguments: str) -> str:
    """
    Function Callを実行して結果を返す
    
    Args:
        name: 関数名
        arguments: JSON形式の引数
    
    Returns:
        実行結果のJSON文字列
    """
    try:
        args = json.loads(arguments)
        
        if name == "get_weather":
            result = await get_weather(
                location=args.get("location", ""),
                date=args.get("date")
            )
            return json.dumps(result, ensure_ascii=False)
        elif name == "search_database":
            result = await search_database(
                query=args.get("query", ""),
                category=args.get("category")
            )
            return json.dumps(result, ensure_ascii=False)
        else:
            return json.dumps({"error": f"Unknown function: {name}"}, ensure_ascii=False)
            
    except Exception as e:
        logger.exception(f"Function call error: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==============================================================================
# WebSocketイベント受信処理
# ==============================================================================
async def _receive_events_loop(connection: AsyncRealtimeConnection):
    """Azure Realtime APIからのイベントを受信・処理するメインループ"""
    try:
        async for event in connection:
            event_type = getattr(event, "type", "")
            
            # =================================================================
            # アシスタントのテキスト応答処理
            # =================================================================
            if event_type == "response.text.delta":
                await _handle_agent_text_delta(getattr(event, "delta", None))
                
            elif event_type == "response.audio_transcript.delta":
                await _handle_agent_audio_transcript_delta(getattr(event, "delta", None))
                
            elif event_type in ("response.text.done", "response.audio_transcript.done"):
                await _finalize_agent_message()
            
            # =================================================================
            # アシスタントの音声出力処理
            # =================================================================
            elif event_type == "response.audio.delta":
                await _handle_agent_audio_delta(getattr(event, "delta", None))
            
            # =================================================================
            # ユーザーの音声転写処理
            # =================================================================
            elif event_type == "conversation.item.input_audio_transcription.delta":
                await _handle_user_transcription_delta(getattr(event, "delta", None))
                
            elif event_type == "conversation.item.input_audio_transcription.completed":
                await _handle_user_transcription_completed(getattr(event, "transcript", ""))
                
            elif event_type == "conversation.item.input_audio_transcription.failed":
                await _handle_user_transcription_failed()
            
            # =================================================================
            # Function Call処理
            # =================================================================
            elif event_type == "response.function_call_arguments.delta":
                await _handle_function_call_arguments_delta(event)
                
            elif event_type == "response.function_call_arguments.done":
                await _handle_function_call_arguments_done(event)
            
            # =================================================================
            # 応答完了・エラー処理
            # =================================================================
            elif event_type == "response.done":
                await _handle_response_done()
                
            elif event_type == "error" or event_type.endswith(".error"):
                await _handle_error_event(event)
                
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception(e)


async def _handle_agent_text_delta(delta: Optional[str]):
    """エージェントのテキスト応答の差分を処理"""
    if isinstance(delta, str) and delta:
        cl.user_session.set(KEY_TEXT_STREAM_LOCKED, True)
        await _stream_agent_text(delta)


async def _handle_agent_audio_transcript_delta(delta: Optional[str]):
    """エージェントの音声転写の差分を処理(テキストがない場合のフォールバック)"""
    if not cl.user_session.get(KEY_TEXT_STREAM_LOCKED):
        if isinstance(delta, str) and delta:
            await _stream_agent_text(delta)


async def _stream_agent_text(delta: str):
    """アシスタントのテキストをUIにストリーム表示"""
    current_msg = cl.user_session.get(KEY_CURRENT_RESPONSE_MSG)
    if current_msg is None:
        current_msg = cl.Message(content="", author="エージェント")
        await current_msg.send()
        cl.user_session.set(KEY_CURRENT_RESPONSE_MSG, current_msg)
    await current_msg.stream_token(delta)


async def _finalize_agent_message():
    """エージェントのメッセージを確定"""
    current_msg = cl.user_session.get(KEY_CURRENT_RESPONSE_MSG)
    if current_msg is not None:
        await current_msg.update()
        cl.user_session.set(KEY_CURRENT_RESPONSE_MSG, None)


async def _handle_agent_audio_delta(raw_b64: Optional[Any]):
    """エージェントの音声データを処理"""
    if isinstance(raw_b64, (str, bytes)) and raw_b64:
        audio_bytes = base64.b64decode(raw_b64)
        
        if not cl.user_session.get(KEY_IS_PLAYING):
            cl.user_session.set(KEY_IS_PLAYING, True)
            
        await cl.context.emitter.send_audio_chunk(
            cl.OutputAudioChunk(
                mimeType="pcm16",
                data=audio_bytes,
                track=_get_track_id(),
            )
        )


async def _handle_user_transcription_delta(delta: Optional[str]):
    """ユーザーの音声転写の差分を処理"""
    if isinstance(delta, str) and delta:
        user_msg = cl.user_session.get(KEY_CURRENT_USER_MSG)
        if user_msg is None:
            user_msg = cl.Message(content="", author="user", type="user_message")
            await user_msg.send()
            cl.user_session.set(KEY_CURRENT_USER_MSG, user_msg)
        await user_msg.stream_token(delta)


async def _handle_user_transcription_completed(final_text: str):
    """ユーザーの音声転写完了を処理"""
    user_msg = cl.user_session.get(KEY_CURRENT_USER_MSG)
    if user_msg is None:
        if isinstance(final_text, str) and final_text.strip():
            await cl.Message(
                content=final_text,
                author="user",
                type="user_message",
            ).send()
    else:
        await user_msg.update()
        cl.user_session.set(KEY_CURRENT_USER_MSG, None)


async def _handle_user_transcription_failed():
    """ユーザーの音声転写失敗を処理"""
    user_msg = cl.user_session.get(KEY_CURRENT_USER_MSG)
    if user_msg is not None:
        await user_msg.update()
        cl.user_session.set(KEY_CURRENT_USER_MSG, None)


async def _handle_function_call_arguments_delta(event):
    """Function Call引数の差分を処理"""
    call_id = getattr(event, "call_id", None)
    delta = getattr(event, "delta", "")
    
    if call_id:
        pending_calls = cl.user_session.get(KEY_PENDING_FUNCTION_CALLS) or {}
        if call_id not in pending_calls:
            pending_calls[call_id] = {
                "name": "",
                "arguments": "",
                "item_id": getattr(event, "item_id", None)
            }
        pending_calls[call_id]["arguments"] += delta
        cl.user_session.set(KEY_PENDING_FUNCTION_CALLS, pending_calls)


async def _handle_function_call_arguments_done(event):
    """Function Call引数の受信完了を処理し、関数を実行して結果を返す"""
    call_id = getattr(event, "call_id", None)
    name = getattr(event, "name", None)
    arguments = getattr(event, "arguments", "")
    item_id = getattr(event, "item_id", None)
    
    if not call_id:
        return
    
    connection: Optional[AsyncRealtimeConnection] = cl.user_session.get(KEY_CONN)
    if connection is None:
        return
    
    # 非同期でFunction Callを実行(ブロッキングしない)
    asyncio.create_task(_execute_function_call_async(connection, call_id, name, arguments))
    
    # Pending Function Callsから削除
    pending_calls = cl.user_session.get(KEY_PENDING_FUNCTION_CALLS) or {}
    if call_id in pending_calls:
        del pending_calls[call_id]
        cl.user_session.set(KEY_PENDING_FUNCTION_CALLS, pending_calls)


async def _execute_function_call_async(
    connection: AsyncRealtimeConnection,
    call_id: str,
    name: str,
    arguments: str
):
    """
    Function Callを非同期で実行(ブロッキングしない)
    
    これにより、長時間実行される関数でも他の処理をブロックしません。
    """
    # Stepを開始(非同期で表示)
    step = cl.Step(name=f"🔧 {name}", type="tool")
    await step.send()
    
    try:
        # 引数を表示
        try:
            args_dict = json.loads(arguments)
            args_display = "\n".join([f"- **{k}**: {v}" for k, v in args_dict.items()])
            step.input = f"**引数:**\n{args_display}"
        except:
            step.input = arguments
        
        await step.update()
        
        # Function Callを実行
        logger.info(f"Executing function: {name} with args: {arguments}")
        result = await _execute_function_call(name, arguments)
        logger.info(f"Function result: {result}")
        
        # 結果を表示
        try:
            result_dict = json.loads(result)
            if "error" in result_dict:
                step.output = f"❌ エラー: {result_dict['error']}"
                step.is_error = True
            else:
                # 結果を整形
                if name == "get_weather" and "forecast" in result_dict:
                    step.output = f"✅ {result_dict['forecast']}"
                elif name == "search_database" and "summary" in result_dict:
                    step.output = f"✅ {result_dict['summary']}\n\n**検索結果:**"
                    for idx, item in enumerate(result_dict.get("results", []), 1):
                        step.output += f"\n{idx}. **{item['title']}** (関連度: {item['relevance']})\n   {item['summary']}"
                else:
                    result_display = "\n".join([f"- **{k}**: {v}" for k, v in result_dict.items() if k != "results"])
                    step.output = f"**結果:**\n{result_display}"
        except:
            step.output = result
        
        await step.update()
        
        # Function Call結果を会話に追加
        await connection.conversation.item.create(
            item={
                "type": "function_call_output",
                "call_id": call_id,
                "output": result,
            }
        )
        
        # 応答生成フラグをチェック
        # すでに生成中でない場合のみ新しい応答を生成
        if not cl.user_session.get(KEY_IS_GENERATING):
            cl.user_session.set(KEY_IS_GENERATING, True)
            # 新しい応答を生成
            await connection.response.create(
                response={
                    "modalities": ["text", "audio"],
                    "instructions": (
                        "Function callの結果を使って、ユーザーに分かりやすく回答してください。"
                    ),
                }
            )
        
    except Exception as e:
        logger.exception(f"Error executing function call: {e}")
        step.output = f"❌ エラーが発生しました: {str(e)}"
        step.is_error = True
        await step.update()
        
        # エラー時もFunction Call結果として返す
        error_result = json.dumps({"error": str(e)}, ensure_ascii=False)
        await connection.conversation.item.create(
            item={
                "type": "function_call_output",
                "call_id": call_id,
                "output": error_result,
            }
        )
        
        if not cl.user_session.get(KEY_IS_GENERATING):
            cl.user_session.set(KEY_IS_GENERATING, True)
            await connection.response.create()


async def _handle_response_done():
    """応答完了を処理"""
    cl.user_session.set(KEY_IS_GENERATING, False)
    cl.user_session.set(KEY_IS_PLAYING, False)
    cl.user_session.set(KEY_TEXT_STREAM_LOCKED, False)
    
    current_msg = cl.user_session.get(KEY_CURRENT_RESPONSE_MSG)
    if current_msg is not None:
        await current_msg.update()
        cl.user_session.set(KEY_CURRENT_RESPONSE_MSG, None)


async def _handle_error_event(event):
    """エラーイベントを処理"""
    cl.user_session.set(KEY_IS_GENERATING, False)
    cl.user_session.set(KEY_TEXT_STREAM_LOCKED, False)
    logger.error(f"[Realtime error] {getattr(event, 'error', event)}")
    
    current_msg = cl.user_session.get(KEY_CURRENT_RESPONSE_MSG)
    if current_msg is not None:
        await current_msg.update()
        cl.user_session.set(KEY_CURRENT_RESPONSE_MSG, None)
        
    current_user_msg = cl.user_session.get(KEY_CURRENT_USER_MSG)
    if current_user_msg is not None:
        await current_user_msg.update()
        cl.user_session.set(KEY_CURRENT_USER_MSG, None)


# ==============================================================================
# Azure Realtime接続セットアップ
# ==============================================================================
async def _setup_azure_realtime():
    """Azure OpenAI Realtimeに接続し、セッションを初期化"""
    client = _create_azure_client()

    # WebSocket接続を確立
    cm: AsyncRealtimeConnectionManager = client.beta.realtime.connect(model=AZURE_DEPLOYMENT)
    connection: AsyncRealtimeConnection = await cm.__aenter__()

    # セッション設定を更新
    await connection.session.update(
        session={
            "modalities": ["text", "audio"],
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": "whisper-1",
                "language": "ja"
            },
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.6,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 700,
                "interrupt_response": False,
                "create_response": True,
            },
            "instructions": (
                "Always respond in Japanese. "
                "ユーザーとは日本語で会話してください。"
                "天気に関する質問があれば、get_weatherツールを使用して正確な情報を提供してください。"
                "データベース検索の依頼があれば、search_databaseツールを使用してください。"
                "重要: search_databaseツールは時間がかかる処理です。"
                "ツールの実行中でも、ユーザーとの会話は継続してください。"
                "例えば「検索を開始しました。結果が出るまで少し時間がかかりますが、他に何かお手伝いできることはありますか?」のように応答できます。"
            ),
            # ツールを定義
            "tools": [
                {
                    "type": "function",
                    "name": "get_weather",
                    "description": "指定された地域の天気予報を取得します。ユーザーが天気について質問したら、このツールを使用してください。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {
                                "type": "string",
                                "description": "天気を調べる地域名(例: 東京、大阪、札幌)"
                            },
                            "date": {
                                "type": "string",
                                "description": "天気を調べる日付(例: 今日、明日、2025-10-05)。指定がない場合は今日の天気を返します。",
                            }
                        },
                        "required": ["location"]
                    }
                },
                {
                    "type": "function",
                    "name": "search_database",
                    "description": (
                        "データベースから情報を検索します。この処理には5-8秒程度かかります。"
                        "ユーザーが何かを調べてほしいと言ったら、このツールを使用してください。"
                        "例: 「AIについて調べて」「機械学習の情報を検索して」など"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "検索クエリ(例: 人工知能、機械学習、データサイエンス)"
                            },
                            "category": {
                                "type": "string",
                                "description": "検索カテゴリ(オプション。例: 技術、ビジネス、研究)",
                            }
                        },
                        "required": ["query"]
                    }
                }
            ],
            "tool_choice": "auto",  # 必要に応じてツールを自動選択
        }
    )

    # イベント受信タスクを開始
    recv_task: asyncio.Task[Any] = asyncio.create_task(_receive_events_loop(connection))

    # セッションに保存
    cl.user_session.set(KEY_CONN, connection)
    cl.user_session.set(KEY_CM, cm)
    cl.user_session.set(KEY_RECV_TASK, recv_task)
    _get_track_id()


# ==============================================================================
# Chainlitイベントハンドラー
# ==============================================================================
@cl.on_chat_start
async def on_chat_start():
    """チャット開始時の初期化"""
    _ensure_session_defaults()
    await cl.Message(
        content=(
            "🎙️ Azure OpenAI Realtimeエージェントです。録音開始ボタンを押して話しかけてください。\n\n"
            "✨ **利用可能な機能**:\n"
            "1. **天気予報** (即座に応答)\n"
            "   - 「東京の天気は?」「明日の大阪の天気を教えて」\n\n"
            "2. **データベース検索** (時間がかかる処理)\n"
            "   - 「AIについて調べて」「機械学習の情報を検索して」\n"
            "   - 検索には約20秒かかりますが、モデルは待機中も会話を継続できます!\n\n"
            "💡 **非同期Function Callingを試してみましょう**:\n"
            "- あなたの音声が自動的にテキストで表示されます\n"
            "- エージェントの回答も音声と一緒にテキスト表示されます"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """テキストメッセージを受信した時の処理"""
    _ensure_session_defaults()

    connection: Optional[AsyncRealtimeConnection] = cl.user_session.get(KEY_CONN)
    if connection is None:
        await cl.Message(content="音声モードを開始してから送信してください!").send()
        return

    # 既存の応答生成/再生を停止
    if cl.user_session.get(KEY_IS_GENERATING) or cl.user_session.get(KEY_IS_PLAYING):
        try:
            await connection.response.cancel()
        except Exception:
            pass
        await cl.context.emitter.send_audio_interrupt()
        cl.user_session.set(KEY_TRACK_ID, str(uuid4()))
        cl.user_session.set(KEY_IS_GENERATING, False)
        cl.user_session.set(KEY_IS_PLAYING, False)

        current_msg = cl.user_session.get(KEY_CURRENT_RESPONSE_MSG)
        if current_msg is not None:
            await current_msg.update()
            cl.user_session.set(KEY_CURRENT_RESPONSE_MSG, None)

    # テキストを会話アイテムとして追加
    await connection.conversation.item.create(
        item={
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": message.content}],
        }
    )

    # 応答生成をリクエスト
    await connection.response.create(
        response={
            "modalities": ["text", "audio"],
            "instructions": (
                "Please provide both text and audio responses. "
                "Always include text transcription with your audio response."
            ),
        }
    )
    cl.user_session.set(KEY_IS_GENERATING, True)


@cl.on_audio_start
async def on_audio_start():
    """音声録音開始時の処理"""
    try:
        _ensure_session_defaults()
        if cl.user_session.get(KEY_CONN) is None:
            await _setup_azure_realtime()
        await cl.Message(content="🎤 音声モードを開始しました。話しかけてください。").send()
        return True
    except Exception as e:
        await cl.ErrorMessage(content=f"Azure Realtime への接続に失敗しました: {e}").send()
        return False


@cl.on_audio_chunk
async def on_audio_chunk(chunk: cl.InputAudioChunk):
    """音声データのチャンクを受信した時の処理"""
    connection: Optional[AsyncRealtimeConnection] = cl.user_session.get(KEY_CONN)
    if connection is None:
        return
    
    b64_audio = base64.b64encode(chunk.data).decode("ascii")
    await connection.input_audio_buffer.append(audio=b64_audio)


@cl.on_audio_end
async def on_audio_end():
    """音声録音終了時の処理"""
    await cl.Message(content="🎤 音声モードを停止しました。話しかける場合は録音開始ボタンを押してください。").send()


@cl.on_chat_end
@cl.on_stop
async def on_chat_end():
    """チャット終了/停止時のクリーンアップ"""
    recv_task: Optional[asyncio.Task[Any]] = cl.user_session.get(KEY_RECV_TASK)
    if recv_task and not recv_task.done():
        recv_task.cancel()
        try:
            await recv_task
        except Exception:
            pass

    cm: Optional[AsyncRealtimeConnectionManager] = cl.user_session.get(KEY_CM)
    if cm is not None:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            pass

    cl.user_session.set(KEY_CONN, None)
    cl.user_session.set(KEY_CM, None)
    cl.user_session.set(KEY_RECV_TASK, None)
    cl.user_session.set(KEY_CURRENT_RESPONSE_MSG, None)
    cl.user_session.set(KEY_CURRENT_USER_MSG, None)