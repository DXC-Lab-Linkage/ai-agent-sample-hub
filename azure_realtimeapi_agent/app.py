import os
import asyncio
import base64
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
        "AZURE_OPENAI_DEPLOYMENT が未設定です。Realtimeモデルのデプロイ名（Deployment name）を設定してください。"
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
KEY_CONN = "azure_realtime_connection"  # WebSocket接続
KEY_RECV_TASK = "azure_realtime_recv_task"  # 受信タスク
KEY_CM = "azure_realtime_connection_manager"  # 接続マネージャー
KEY_TRACK_ID = "track_id"  # 音声トラックID

# 状態管理フラグ
KEY_IS_GENERATING = "is_generating"  # エージェント応答生成中
KEY_IS_PLAYING = "is_playing"  # 音声再生中

# メッセージストリーミング管理
KEY_CURRENT_RESPONSE_MSG = "current_response_message"  # エージェント側のストリーム
KEY_CURRENT_USER_MSG = "current_user_message"  # ユーザー字幕のストリーム
KEY_TEXT_STREAM_LOCKED = "text_stream_locked"  # テキストストリームの重複を防ぐロック


def _ensure_session_defaults():
    """セッションのデフォルト値を設定"""
    defaults = {
        KEY_IS_GENERATING: False,
        KEY_IS_PLAYING: False,
        KEY_TRACK_ID: str(uuid4()),
        KEY_TEXT_STREAM_LOCKED: False,
        KEY_CURRENT_RESPONSE_MSG: None,
        KEY_CURRENT_USER_MSG: None,
    }

    for key, default_value in defaults.items():
        if cl.user_session.get(key) is None:
            cl.user_session.set(key, default_value)


def _get_track_id() -> str:
    """音声トラックIDを取得（存在しない場合は生成）"""
    tid = cl.user_session.get(KEY_TRACK_ID)
    if not isinstance(tid, str) or not tid:
        tid = str(uuid4())
        cl.user_session.set(KEY_TRACK_ID, tid)
    return tid


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
                await _handle_agent_audio_transcript_delta(
                    getattr(event, "delta", None)
                )

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
                await _handle_user_transcription_completed(
                    getattr(event, "transcript", "")
                )

            elif event_type == "conversation.item.input_audio_transcription.failed":
                await _handle_user_transcription_failed()

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
    """エージェントの音声転写の差分を処理（テキストがない場合のフォールバック）"""
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
        # Base64 → PCM16 バイト変換
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
            # 右側のユーザーバブルを空で立ち上げる
            user_msg = cl.Message(content="", author="user", type="user_message")
            await user_msg.send()
            cl.user_session.set(KEY_CURRENT_USER_MSG, user_msg)
        await user_msg.stream_token(delta)


async def _handle_user_transcription_completed(final_text: str):
    """ユーザーの音声転写完了を処理"""
    user_msg = cl.user_session.get(KEY_CURRENT_USER_MSG)
    if user_msg is None:
        # ストリームがなかった場合、最終テキストで新規メッセージ作成
        if isinstance(final_text, str) and final_text.strip():
            await cl.Message(
                content=final_text,
                author="user",
                type="user_message",
            ).send()
    else:
        # ストリーム中のメッセージを確定
        await user_msg.update()
        cl.user_session.set(KEY_CURRENT_USER_MSG, None)


async def _handle_user_transcription_failed():
    """ユーザーの音声転写失敗を処理"""
    user_msg = cl.user_session.get(KEY_CURRENT_USER_MSG)
    if user_msg is not None:
        await user_msg.update()
        cl.user_session.set(KEY_CURRENT_USER_MSG, None)


async def _handle_response_done():
    """応答完了を処理"""
    cl.user_session.set(KEY_IS_GENERATING, False)
    cl.user_session.set(KEY_IS_PLAYING, False)
    cl.user_session.set(KEY_TEXT_STREAM_LOCKED, False)

    # 未確定のメッセージがあれば確定
    current_msg = cl.user_session.get(KEY_CURRENT_RESPONSE_MSG)
    if current_msg is not None:
        await current_msg.update()
        cl.user_session.set(KEY_CURRENT_RESPONSE_MSG, None)


async def _handle_error_event(event):
    """エラーイベントを処理"""
    cl.user_session.set(KEY_IS_GENERATING, False)
    cl.user_session.set(KEY_TEXT_STREAM_LOCKED, False)
    logger.error(f"[Realtime error] {getattr(event, 'error', event)}")

    # 進行中のメッセージをクリーンアップ
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
    cm: AsyncRealtimeConnectionManager = client.beta.realtime.connect(
        model=AZURE_DEPLOYMENT
    )
    connection: AsyncRealtimeConnection = await cm.__aenter__()

    # セッション設定を更新
    await connection.session.update(
        session={
            "modalities": ["text", "audio"],
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": "whisper-1",
                "language": "ja",  # 日本語を明示的に指定
            },
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.6,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 700,
                "interrupt_response": False,  # 割り込み無効化
                "create_response": True,
            },
            "instructions": "Always respond in Japanese. ユーザーとは日本語で会話してください。",
        }
    )

    # イベント受信タスクを開始
    recv_task: asyncio.Task[Any] = asyncio.create_task(_receive_events_loop(connection))

    # セッションに保存
    cl.user_session.set(KEY_CONN, connection)
    cl.user_session.set(KEY_CM, cm)
    cl.user_session.set(KEY_RECV_TASK, recv_task)
    _get_track_id()  # トラックIDを初期化


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
            "- あなたの音声が自動的にテキストで表示されます。\n"
            "- エージェントの回答も音声と一緒にテキスト表示されます。\n"
            "- エージェントの回答中は新しい質問の入力をお待ちください。"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """テキストメッセージを受信した時の処理"""
    _ensure_session_defaults()

    connection: Optional[AsyncRealtimeConnection] = cl.user_session.get(KEY_CONN)
    if connection is None:
        await cl.Message(content="音声モードを開始してから送信してください！").send()
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

        # 進行中のメッセージをクリーンアップ
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
        await cl.Message(
            content="🎤 音声モードを開始しました。話しかけてください。"
        ).send()
        return True
    except Exception as e:
        await cl.ErrorMessage(
            content=f"Azure Realtime への接続に失敗しました: {e}"
        ).send()
        return False


@cl.on_audio_chunk
async def on_audio_chunk(chunk: cl.InputAudioChunk):
    """音声データのチャンクを受信した時の処理"""
    connection: Optional[AsyncRealtimeConnection] = cl.user_session.get(KEY_CONN)
    if connection is None:
        return

    # PCM16データをBase64エンコードしてサーバーに送信
    b64_audio = base64.b64encode(chunk.data).decode("ascii")
    await connection.input_audio_buffer.append(audio=b64_audio)


@cl.on_audio_end
async def on_audio_end():
    """音声録音終了時の処理"""
    # サーバーVAD運用では明示的なコミットは不要
    # 録音停止通知のみ
    await cl.Message(
        content="🎤 音声モードを停止しました。話しかける場合は録音開始ボタンを押してください。"
    ).send()


@cl.on_chat_end
@cl.on_stop
async def on_chat_end():
    """チャット終了/停止時のクリーンアップ"""
    # 受信タスクを停止
    recv_task: Optional[asyncio.Task[Any]] = cl.user_session.get(KEY_RECV_TASK)
    if recv_task and not recv_task.done():
        recv_task.cancel()
        try:
            await recv_task
        except Exception:
            pass

    # 接続を閉じる
    cm: Optional[AsyncRealtimeConnectionManager] = cl.user_session.get(KEY_CM)
    if cm is not None:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            pass

    # セッションをクリア
    cl.user_session.set(KEY_CONN, None)
    cl.user_session.set(KEY_CM, None)
    cl.user_session.set(KEY_RECV_TASK, None)
    cl.user_session.set(KEY_CURRENT_RESPONSE_MSG, None)
    cl.user_session.set(KEY_CURRENT_USER_MSG, None)
