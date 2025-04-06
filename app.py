import json
import os
from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv
from aipolabs import ACI, meta_functions
from aipolabs.types.functions import FunctionDefinitionFormat
from openai import OpenAI

load_dotenv()

# Load required environment variables
LINKED_ACCOUNT_OWNER_ID = os.getenv("LINKED_ACCOUNT_OWNER_ID", "")
if not LINKED_ACCOUNT_OWNER_ID:
    raise ValueError("LINKED_ACCOUNT_OWNER_ID is not set")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")  # e.g., 'whatsapp:+14155238886'

# Initialize your API clients
openai = OpenAI()  # This uses OPENAI_API_KEY from the environment
aci = ACI()        # This uses AIPOLABS_ACI_API_KEY from the environment

# Define your prompt and tools for the ACI chain
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

# Maintain a chat history (optional: you can use a persistent store)
chat_history = []

# Initialize the Flask app
app = Flask(__name__)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    # Extract incoming message from Twilio webhook
    incoming_msg = request.values.get("Body", "")
    sender = request.values.get("From", "")

    # Prepare the LLM message sequence using the incoming WhatsApp text
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": incoming_msg},
    ] + chat_history

    # Generate a response with potential tool call using your ACI chain
    response_data = openai.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        tools=tools_meta,
        tool_choice="required",  # Force the model to generate a tool call if needed
        parallel_tool_calls=False,
    )

    # Extract the text and any tool call from the response
    content = response_data.choices[0].message.content
    tool_call = (
        response_data.choices[0].message.tool_calls[0]
        if response_data.choices[0].message.tool_calls
        else None
    )

    reply_text = content  # Default reply text

    # If a tool call is made, execute it and append the result to the reply
    if tool_call:
        result = aci.handle_function_call(
            tool_call.function.name,
            json.loads(tool_call.function.arguments),
            linked_account_owner_id=LINKED_ACCOUNT_OWNER_ID,
            allowed_apps_only=True,
            format=FunctionDefinitionFormat.OPENAI,
        )
        reply_text = f"{content}\nFunction result: {result}"
        # Update chat history with tool call information
        chat_history.append({"role": "assistant", "tool_calls": [tool_call]})
        chat_history.append(
            {"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(result)}
        )
    else:
        chat_history.append({"role": "assistant", "content": content})

    # Use Twilio's MessagingResponse to send the reply back to WhatsApp
    resp = MessagingResponse()
    resp.message(reply_text)
    return str(resp), 200

if __name__ == "__main__":
    app.run(port=5000)
