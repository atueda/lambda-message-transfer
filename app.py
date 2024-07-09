# serverless-python-requirements を使って zip 圧縮しておいた依存ライブラリの読み込み
# それを使用しない場合はここのコードは削除しても構いません
try:
    import unzip_requirements
except ImportError:
    pass

import logging
import os
import requests
from slack_bolt import App, Ack
from slack_sdk.web import WebClient
from datetime import datetime
import boto3

# ロギング設定
logger = logging.getLogger()
logger.setLevel("INFO")

# 動作確認用にデバッグのロギングを有効にします
# 本番運用では削除しても構いません
logging.basicConfig(level=logging.DEBUG)

# Slackクライアントのセットアップ
client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])

app = App(
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
    token=os.environ["SLACK_BOT_TOKEN"],
    process_before_response=True,
)

channel = os.environ["CHANNEL"]

# S3クライアントのセットアップ
s3_client = boto3.client('s3')
bucket_name = os.environ["S3_BUCKET_NAME"]

# グローバルショットの関数
def just_ack(ack: Ack):
    ack()

# タイムスタンプをフォーマットする関数
def format_timestamp(ts):
    dt = datetime.fromtimestamp(float(ts))
    return dt.strftime('%Y/%m/%d %H:%M:%S')

# メッセージからファイルを取得する関数
def get_files_from_messages(messages):
    files = []
    for message in messages:
        files.extend(message.get("files", []))
    return files

# グローバルショットの処理
def start_modal_interaction(body: dict, client: WebClient):
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "modal-id",
            "title": {"type": "plain_text", "text": "My App"},
            "submit": {"type": "plain_text", "text": "Submit"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "element": {"type": "plain_text_input"},
                    "label": {"type": "plain_text", "text": "Text"},
                },
            ],
        },
    )

def message_shortcut(ack, shortcut, client, body):
    try:
        ack()

        # ユーザー情報を取得
        user_id = shortcut["user"]["id"]
        user_info = client.users_info(user=user_id)
        user_name = user_info["user"]["real_name"]

        # チャンネルIDとメッセージタイムスタンプを取得
        channel_id = shortcut["channel"]["id"]
        message_ts = shortcut["message"]["ts"]
        thread_ts = shortcut["message"].get("thread_ts")

        # チャンネルが指定されたチャンネルであれば処理を終了
        if channel_id == channel:
            print('終了')
            return

        # メッセージの詳細を取得
        message_response = client.conversations_history(
            channel=channel_id,
            latest=message_ts,
            inclusive=True,
            limit=1  # 取得するメッセージを1つに制限
        )
        
        logger.info('## OK1')
        
        message = message_response["messages"][0]
        message_user_id = message["user"]
        message_time = message["ts"]
        date = format_timestamp(message_time)
        message_link = f"https://slack.com/archives/{channel_id}/p{message_time.replace('.', '')}"
        message_user_info = client.users_info(user=message_user_id)
        message_user_name = message_user_info["user"]["real_name"]
        message_files = get_files_from_messages([message])
        
        logger.info('## OK2')

        # コンテンツの生成
        content = f"このメッセージ保存を実行したユーザー: {user_name} (<@{user_id}>)"
        content += (f"\n\n投稿者: {message_user_name} (<@{message_user_id}>)\n"
                   f"日時: {date}\n"
                   f"リンク: {message_link}\n"
                   f"メッセージ:\n{message['text']}\n\n")

        logger.info('## OK3')

        #スレッドが存在する場合、スレッドの詳細を追加
        if thread_ts:
            thread_response = client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                inclusive=True
            )
            thread_files = get_files_from_messages(thread_response["messages"])
            thread_messages = thread_response["messages"]
            thread_text = "\n".join([f"投稿者:<@{thread['user']}>\n日時: {format_timestamp(thread['ts'])}\nメッセージ {i}: {thread['text']}" for i, thread in enumerate(thread_messages[1:], 1)])
            content += f"スレッド:\n{thread_text}"
        
        logger.info('## OK4')

        # テキストコンテンツをS3に保存
        s3_key = f"messages/{channel_id}/{message_ts}.txt"
        s3_client.put_object(Bucket=bucket_name, Key=s3_key, Body=content)
        
        logger.info('## OK5')

        # 新しいメッセージを別のチャンネルに投稿
        new_message = client.chat_postMessage(
            channel=channel,
            text=content
        )
        new_thread_ts = new_message["ts"]

        # 元のメッセージとスレッド内のファイルを新しいメッセージのスレッドに添付
        all_files = message_files + (thread_files if thread_ts else [])
        for file in all_files:
            file_id = file["id"]
            file_info = client.files_info(file=file_id)
            file_name = file_info["file"]["name"]
            file_url = file_info["file"]["url_private"]
            response = requests.get(file_url, headers={"Authorization": f"Bearer {os.environ.get('SLACK_BOT_TOKEN')}"})
            file_content = response.content

            # ファイルをS3に保存
            s3_file_key = f"files/{file_id}/{file_name}"
            s3_client.put_object(Bucket=bucket_name, Key=s3_file_key, Body=file_content)
            logger.info(f"Uploaded file to S3: {s3_file_key}")

            # Slackにファイルをアップロード
            client.files_upload_v2(
                channels=channel,
                file=file_content,
                filename=file_name,
                thread_ts=new_thread_ts
            )
        
        logger.info('## OK6')
        
        # 元のスレッドにリアクションを追加
        client.reactions_add(
            channel=channel_id,
            name="white_check_mark",
            timestamp=message_ts
        )
        pass
    except Exception as e:
        logger.error(e)

# モーダルで送信ボタンが押されたときに呼び出される処理
def handle_modal(ack: Ack):
    ack()

# モーダルで送信ボタンが押されたときに非同期で処理される関数
def handle_time_consuming_task(logger: logging.Logger, view: dict):
    logger.info(view)

# @app.view のようなデコレーターでの登録ではなく
# Lazy Listener としてメインの処理を設定します
app.shortcut("run-aws-lambda-app")(
  ack=just_ack,
  lazy=[start_modal_interaction],
)
app.view("modal-id")(
  ack=handle_modal,
  lazy=[handle_time_consuming_task],
)

# 他の処理を追加するときはここに追記してください
app.shortcut("message_save")(
    ack=just_ack,
    lazy=[message_shortcut],
)

if __name__ == "__main__":
    app.start(3000)
    
# AWS Lambda 環境で実行される関数
from slack_bolt.adapter.aws_lambda import SlackRequestHandler

# ロギングを AWS Lambda 向けに初期化します
SlackRequestHandler.clear_all_log_handlers()
logging.basicConfig(format="%(asctime)s %(message)s", level=logging.DEBUG)

def handler(event, context):
    slack_handler = SlackRequestHandler(app=app)
    return slack_handler.handle(event, context)
