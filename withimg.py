import json
import os
import requests
import base64

from flask import Flask, request
from dotenv import load_dotenv
from twilio.rest import Client
from aipolabs import ACI, meta_functions
from aipolabs.types.functions import FunctionDefinitionFormat
from openai import OpenAI
from rich import print as rprint
from rich.panel import Panel

# Cloudinary imports and configuration
import cloudinary
import cloudinary.uploader
from cloudinary.utils import cloudinary_url

# Configure Cloudinary using your provided credentials.
cloudinary.config( 
    cloud_name = "dnbihhsqo", 
    api_key = "527114253711376", 
    api_secret = "C5JeNZ_1U8GVt7H0TCLPDCJrYCQ", 
    secure=True
)

# Load environment variables for other credentials
load_dotenv()
LINKED_ACCOUNT_OWNER_ID = os.getenv("LINKED_ACCOUNT_OWNER_ID", "")
if not LINKED_ACCOUNT_OWNER_ID:
    raise ValueError("LINKED_ACCOUNT_OWNER_ID is not set")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
    raise ValueError("Twilio credentials are not set properly")

# Initialize clients: Twilio, OpenAI, and ACI.
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
openai = OpenAI()
aci = ACI()

# Define the system prompt for the LLM.
prompt = (
    "You are a helpful assistant with access to a unlimited number of tools via four meta functions: "
    "ACI_SEARCH_APPS, ACI_SEARCH_FUNCTIONS, ACI_GET_FUNCTION_DEFINITION, and ACI_EXECUTE_FUNCTION. "
    "You can use ACI_SEARCH_APPS to find relevant apps (which include a set of functions). "
    "If you find apps that might help with your tasks you can use ACI_SEARCH_FUNCTIONS to find functions within those apps, "
    "or search for functions directly across all apps. Once you identify the function you need, use ACI_GET_FUNCTION_DEFINITION "
    "to obtain its definition, and then ACI_EXECUTE_FUNCTION to run it with the correct input arguments. "
    "The typical order is ACI_SEARCH_APPS -> ACI_SEARCH_FUNCTIONS -> ACI_GET_FUNCTION_DEFINITION -> ACI_EXECUTE_FUNCTION."
)

tools_meta = [
    meta_functions.ACISearchApps.SCHEMA,
    meta_functions.ACISearchFunctions.SCHEMA,
    meta_functions.ACIGetFunctionDefinition.SCHEMA,
    meta_functions.ACIExecuteFunction.SCHEMA,
]

app = Flask(__name__)

def upload_image_to_cloudinary(image_binary):
    """
    Upload the image binary to Cloudinary and return a public secure URL.
    """
    try:
        upload_result = cloudinary.uploader.upload(image_binary, resource_type="image")
        public_url = upload_result["secure_url"]
        rprint(Panel(f"Uploaded image URL: {public_url}", style="bold green"))
        return public_url
    except Exception as e:
        rprint(Panel(f"Error uploading to Cloudinary: {e}", style="bold red"))
        return None

@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    # Extract incoming message and sender info.
    original_msg = request.values.get("Body", "")
    rprint(Panel(f"Received message: {original_msg}", style="bold green"))
    from_number = request.values.get("From", "")
    rprint(Panel(f"Received message from: {from_number}", style="bold cyan"))
    incoming_msg = original_msg

    chat_history = []
    final_result = None

    # --- Conversation Loop with the LLM ---
    while True:
        rprint(Panel("Waiting for LLM output...", style="bold blue"))
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
        tool_calls = getattr(message, "tool_calls", None)
        tool_call = tool_calls[0] if tool_calls else None

        if content:
            rprint(Panel("LLM Message", style="bold green"))
            rprint(content)
            chat_history.append({"role": "assistant", "content": content})
            final_result = content
            break

        if tool_call:
            rprint(Panel(f"Function call: {tool_call.function.name}", style="bold yellow"))
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

    if isinstance(final_result, dict):
        final_result_str = json.dumps(final_result)
    else:
        final_result_str = str(final_result)

    # --- Send the Final Text Result via Twilio ---
    # text_message = twilio_client.messages.create(
    #     body=f"Final result:\n\n{final_result_str}",
    #     from_=TWILIO_PHONE_NUMBER,
    #     to=from_number
    # )
    # rprint(Panel(f"Sent text message SID: {text_message.sid}", style="bold cyan"))

    # --- Automatic Image Generation Section ---
    image_api_url = "https://api.venice.ai/api/v1/image/generate"
    payload = json.dumps({
        "model": "fluently-xl",
        "prompt": final_result_str if final_result_str else "Default prompt if empty",
        "width": 1024,
        "height": 1024,
        "safe_mode": False,
        "hide_watermark": False,
        "cfg_scale": 7,
        "negative_prompt": "text",
        "return_binary": True,  # We expect binary output.
        "format": "png",
    })
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer UBYHK4Ii4OInFupNjKVtYh6shEZ_Ftu'
    }
    rprint(Panel("Generating image via Venice API (expecting binary data)", style="bold blue"))
    img_response = requests.request("POST", image_api_url, headers=headers, data=payload)
    rprint(Panel(f"API Response headers: {img_response.headers}", style="bold magenta"))

    base64_image = None
    
    content_type = img_response.headers.get("Content-Type", "")
    if "application/json" in content_type:
        try:
            img_data = img_response.json()
            if "image_base64" in img_data:
                base64_image = img_data.get("image_base64")
            elif "image_hex" in img_data:
                hex_image = img_data.get("image_hex")
                base64_image = base64.b64encode(bytes.fromhex(hex_image)).decode("utf-8")
            else:
                rprint(Panel("No known image data keys in JSON response.", style="bold red"))
        except Exception as e:
            rprint(Panel(f"Error parsing JSON: {e}", style="bold red"))
    else:
        try:
            image_binary = img_response.content
            base64_image = base64.b64encode(image_binary).decode("utf-8")
        except Exception as e:
            rprint(Panel(f"Error converting binary data: {e}", style="bold red"))

    # --- Send the Generated Image ---
    if base64_image and len(base64_image) > 1500:  # If too long to send as text
        rprint(Panel("Base64 image string too long. Uploading to Cloudinary...", style="bold yellow"))
        # Convert the base64 string back to binary for upload.
        image_binary = base64.b64decode(base64_image)
        public_url = upload_image_to_cloudinary(image_binary)
        if public_url:
            # Send image as a media message using the public URL.
            image_message = twilio_client.messages.create(
                body={final_result_str},
                media_url=[public_url],
                from_=TWILIO_PHONE_NUMBER,
                to=from_number
            )
            rprint(Panel(f"Sent image message with media_url. SID: {image_message.sid}", style="bold cyan"))
        else:
            rprint(Panel("Failed to upload image to Cloudinary.", style="bold red"))
    elif base64_image:
        # If the image is short, send as text (not recommended for production).
        image_message = twilio_client.messages.create(
            body=f"Here is your generated image (base64 encoded):\n\n{base64_image}",
            from_=TWILIO_PHONE_NUMBER,
            to=from_number
        )
        rprint(Panel(f"Sent image message as text. SID: {image_message.sid}", style="bold cyan"))
    else:
        rprint(Panel("No image to send.", style="bold red"))

    return "OK", 200

if __name__ == "__main__":
    app.run(port=5000)
