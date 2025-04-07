import json
import os

from flask import Flask, request, Response
from dotenv import load_dotenv
from twilio.rest import Client
from aipolabs import ACI, meta_functions
from aipolabs.types.functions import FunctionDefinitionFormat
from openai import OpenAI
from rich import print as rprint
from rich.panel import Panel

load_dotenv()
LINKED_ACCOUNT_OWNER_ID = os.getenv("LINKED_ACCOUNT_OWNER_ID", "")
if not LINKED_ACCOUNT_OWNER_ID:
    raise ValueError("LINKED_ACCOUNT_OWNER_ID is not set")

# Twilio credentials from environment variables
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
    raise ValueError("Twilio credentials are not set properly")

# Initialize Twilio REST client
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Initialize OpenAI and ACI clients
openai = OpenAI()
aci = ACI()

prompt = (
    "You are a helpful assistant with access to a unlimited number of tools via four meta functions: "
    "ACI_SEARCH_APPS, ACI_SEARCH_FUNCTIONS, ACI_GET_FUNCTION_DEFINITION, and ACI_EXECUTE_FUNCTION. "
    "You can use ACI_SEARCH_APPS to find relevant apps (which include a set of functions), if you find Apps that might help with your tasks you can use ACI_SEARCH_FUNCTIONS to find relevant functions within certain apps. "
    "You can also use ACI_SEARCH_FUNCTIONS directly to find relevant functions across all apps. "
    "Once you have identified the function you need to use, you can use ACI_GET_FUNCTION_DEFINITION to get the definition of the function. "
    "You can then use ACI_EXECUTE_FUNCTION to execute the function provided you have the correct input arguments. "
    "So the typical order is ACI_SEARCH_APPS -> ACI_SEARCH_FUNCTIONS -> ACI_GET_FUNCTION_DEFINITION -> ACI_EXECUTE_FUNCTION."
)

tools_meta = [
    meta_functions.ACISearchApps.SCHEMA,
    meta_functions.ACISearchFunctions.SCHEMA,
    meta_functions.ACIGetFunctionDefinition.SCHEMA,
    meta_functions.ACIExecuteFunction.SCHEMA,
]

app = Flask(__name__)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    # Extract the incoming message and sender's phone number from the request.
    incoming_msg = request.values.get("Body", "")
    from_number = request.values.get("From", "")
    rprint(Panel(f"Received message from: {from_number}", style="bold cyan"))
    
    chat_history = []
    final_result = None

    # Process the conversation loop with the LLM.
    while True:
        rprint(Panel("Waiting for LLM Output", style="bold blue"))
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": incoming_msg},
            ] + chat_history,
            tools=tools_meta,
            parallel_tool_calls=False,
        )

        message = response.choices[0].message
        content = message.content
        tool_calls = message.tool_calls if hasattr(message, 'tool_calls') else None
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

    # Prepare final result as string.
    if isinstance(final_result, dict):
        final_result_str = json.dumps(final_result)
    else:
        final_result_str = str(final_result)

    # Send the result manually via Twilio REST API.
    message = twilio_client.messages.create(
        body=final_result_str,
        from_=TWILIO_PHONE_NUMBER,
        to=from_number
    )
    rprint(Panel(f"Sent message SID: {message.sid}", style="bold cyan"))

    # Return a simple response to acknowledge the webhook.
    return "OK", 200

if __name__ == "__main__":
    app.run(port=5000)
