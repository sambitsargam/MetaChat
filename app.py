import json
import os
import logging

from flask import Flask, request, Response, session
from dotenv import load_dotenv
from twilio.rest import Client
from aipolabs import ACI, meta_functions
from aipolabs.types.functions import FunctionDefinitionFormat
from openai import OpenAI
from rich import print as rprint
from rich.panel import Panel

# Load environment variables
load_dotenv()

# Configurations and validation
LINKED_ACCOUNT_OWNER_ID = os.getenv("LINKED_ACCOUNT_OWNER_ID", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "default_secret")
PORT = int(os.getenv("PORT", 5000))

if not LINKED_ACCOUNT_OWNER_ID:
    raise ValueError("LINKED_ACCOUNT_OWNER_ID is not set")
if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
    raise ValueError("Twilio credentials are not set properly")

# Initialize clients
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
openai = OpenAI()
aci = ACI()

# Flask setup
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# Logging setup
logging.basicConfig(level=logging.INFO)

# Meta Functions Setup
prompt = (
    "You are a helpful assistant with access to a unlimited number of tools via four meta functions: "
    "ACI_SEARCH_APPS, ACI_SEARCH_FUNCTIONS, ACI_GET_FUNCTION_DEFINITION, and ACI_EXECUTE_FUNCTION. "
    "Use them as needed to complete user tasks."
)
tools_meta = [
    meta_functions.ACISearchApps.SCHEMA,
    meta_functions.ACISearchFunctions.SCHEMA,
    meta_functions.ACIGetFunctionDefinition.SCHEMA,
    meta_functions.ACIExecuteFunction.SCHEMA,
]

def get_chat_history(user_id):
    return session.get(user_id, [])

def save_chat_history(user_id, history):
    session[user_id] = history

def send_message(body, to):
    try:
        msg = twilio_client.messages.create(body=body, from_=TWILIO_PHONE_NUMBER, to=to)
        logging.info(f"Sent message SID: {msg.sid}")
    except Exception as e:
        logging.error(f"Failed to send message to {to}: {e}")

@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "").strip()

    if not incoming_msg or not from_number:
        logging.error("Missing incoming message or from number.")
        return Response("Invalid request", status=400)

    # Command handling
    if incoming_msg.lower() == "/help":
        help_message = "Send me a task to perform.\nCommands: /help, /reset, /status"
        send_message(help_message, from_number)
        return "OK", 200

    if incoming_msg.lower() == "/reset":
        save_chat_history(from_number, [])
        send_message("Session has been reset.", from_number)
        return "OK", 200

    if incoming_msg.lower() == "/status":
        history = get_chat_history(from_number)
        status_message = f"Session has {len(history)} message(s)."
        send_message(status_message, from_number)
        return "OK", 200

    # Adjust prompt for "generate" prefix
    if incoming_msg.lower().startswith("generate"):
        incoming_msg = " ".join(incoming_msg.split()[2:])

    rprint(Panel(f"Received message: {incoming_msg}", style="bold green"))
    rprint(Panel(f"From: {from_number}", style="bold cyan"))

    chat_history = get_chat_history(from_number)
    final_result = None

    try:
        while True:
            rprint(Panel("Waiting for LLM Output", style="bold blue"))
            response = openai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": prompt},
                          {"role": "user", "content": incoming_msg}] + chat_history,
                tools=tools_meta,
                parallel_tool_calls=False,
            )

            message = response.choices[0].message
            content = message.content
            tool_calls = getattr(message, 'tool_calls', None)
            tool_call = tool_calls[0] if tool_calls else None

            if content:
                rprint(Panel("LLM Message", style="bold green"))
                rprint(content)
                chat_history.append({"role": "assistant", "content": content})
                final_result = content
                break

            if tool_call:
                rprint(Panel(f"Function Call: {tool_call.function.name}", style="bold yellow"))
                rprint(f"Arguments: {tool_call.function.arguments}")
                chat_history.append({"role": "assistant", "tool_calls": [tool_call]})
                result = aci.handle_function_call(
                    tool_call.function.name,
                    json.loads(tool_call.function.arguments),
                    linked_account_owner_id=LINKED_ACCOUNT_OWNER_ID,
                    allowed_apps_only=True,
                    format=FunctionDefinitionFormat.OPENAI,
                )
                rprint(Panel("Function Call Result", style="bold magenta"))
                rprint(result)
                chat_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                })
                final_result = result
            else:
                rprint(Panel("Task Completed", style="bold green"))
                break

        save_chat_history(from_number, chat_history)

        # Prepare and send the final result
        if isinstance(final_result, dict):
            final_result_str = json.dumps(final_result, indent=2)
        else:
            final_result_str = str(final_result)

        send_message(final_result_str, from_number)
        return "OK", 200

    except Exception as e:
        logging.error(f"Error processing request: {e}")
        send_message("An error occurred. Please try again later.", from_number)
        return Response("Internal Server Error", status=500)

if __name__ == "__main__":
    app.run(port=PORT)
