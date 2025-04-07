import json
import os

from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv
from aipolabs import ACI, meta_functions
from aipolabs.types.functions import FunctionDefinitionFormat
from openai import OpenAI
from rich import print as rprint
from rich.panel import Panel

load_dotenv()
LINKED_ACCOUNT_OWNER_ID = os.getenv("LINKED_ACCOUNT_OWNER_ID", "")
if not LINKED_ACCOUNT_OWNER_ID:
    raise ValueError("LINKED_ACCOUNT_OWNER_ID is not set")

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
    # Receive incoming prompt from WhatsApp
    incoming_msg = request.values.get("Body", "")
    chat_history = []
    final_result = None

    while True:
        rprint(Panel("Waiting for LLM Output", style="bold blue"))
        response = openai.chat.completions.create(
            model="gpt-4o-2024-08-06",
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
            rprint(f"arguments: {tool_call.function.arguments}")
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
            chat_history.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )
            final_result = result
        else:
            rprint(Panel("Task Completed", style="bold green"))
            break

    # Create Twilio MessagingResponse and explicitly set Content-Type as "application/xml"
    resp = MessagingResponse()
    if isinstance(final_result, dict):
        final_result_str = json.dumps(final_result)
    else:
        final_result_str = str(final_result)
    resp.message(final_result_str)
    return Response(str(resp), mimetype="application/xml"), 200

if __name__ == "__main__":
    app.run(port=5000)
