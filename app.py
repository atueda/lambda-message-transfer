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
#from weasyprint import HTML  # WeasyPrintのインポート
#import pdf

logger = logging.getLogger()
logger.setLevel("INFO")

# 動作確認用にデバッグのロギングを有効にします
# 本番運用では削除しても構いません
logging.basicConfig(level=logging.DEBUG)

# Slackクライアントのセットアップ
client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])

app = App(
    # リクエストの検証に必要な値
    # Settings > Basic Information > App Credentials > Signing Secret で取得可能な値
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
    # 上でインストールしたときに発行されたアクセストークン
    # Settings > Install App で取得可能な値
    token=os.environ["SLACK_BOT_TOKEN"],
    # AWS Lamdba では、必ずこの設定を true にしておく必要があります
    process_before_response=True,
)

channel=os.environ["CHANNEL"]

# グローバルショットの関数
# lazy に指定された関数は別の AWS Lambda 実行として非同期で実行されます
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
    # 入力項目ひとつだけのシンプルなモーダルを開く
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
        
        # PDFを生成
        # pdf_file_path = "message.pdf"
        # HTML(string=content).write_pdf(pdf_file_path)  # WeasyPrintでPDFを生成
        # logger.info("File Create")
        
        # 新しいメッセージを別のチャンネルに投稿し、そのスレッドにPDFを添付
        new_message = client.chat_postMessage(
            channel=channel,
            text=content
        )
        new_thread_ts = new_message["ts"]
        # client.files_upload_v2(
        #     channels=channel,
        #     file=pdf_file_path,
        #     title="Message and Thread PDF",
        #     initial_comment="Here is the PDF containing the message and its thread.",
        #     thread_ts=new_thread_ts
        # )
        logger.info('## OK5')
        
        # 元のメッセージとスレッド内のファイルを新しいメッセージのスレッドに添の
        all_files = message_files + (thread_files if thread_ts else [])
        for file in all_files:
            file_id = file["id"]
            file_info = client.files_info(file=file_id)
            file_name = file_info["file"]["name"]
            file_url = file_info["file"]["url_private"]
            response = requests.get(file_url, headers={"Authorization": f"Bearer {os.environ.get('SLACK_BOT_TOKEN')}"})
            file_content = response.content
            response = client.files_upload_v2(
                channels=channel,
                file=file_content,
                filename=file_name,
                thread_ts=new_thread_ts
            )
            logger.info(f"File upload response: {response}")
    
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
# このメソッドは 3 秒以内に終了しなければならない
def handle_modal(ack: Ack):
    # ack() は何も渡さず呼ぶとただ今のモーダルを閉じるだけ
    # response_action とともに応のがダメでのがダメな
    # エラーを表示したり、モーダルの内容を更新したりできる
    # https://slack.dev/bolt-python/ja-jp/concepts#view_submissions
    ack()

# モーダルで送信ボタンが押されたときに非のがダメな処理
# モーダルの操作以外で時間のかかる処理があればこちらに書く
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
    # python app.py のように実行すると開発用 Web サーバーで起動します
    app.start(3000)
    
# これより以降は AWS Lambda 環境で実行したときのみ実行されます

from slack_bolt.adapter.aws_lambda import SlackRequestHandler

# ロギングを AWS Lambda 向けに初期化します
SlackRequestHandler.clear_all_log_handlers()
logging.basicConfig(format="%(asctime)s %(message)s", level=logging.DEBUG)

# AWS Lambda 環境で実行される関数
def handler(event, context):
    # AWS Lambda 環境のリクエスト情報を app が処理できるよう変換してくれるアダプター
    slack_handler = SlackRequestHandler(app=app)
    # 応答はのがダメでのがダメのがダメなやり方を
    return slack_handler.handle(event, context)
