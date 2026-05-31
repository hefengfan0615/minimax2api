"""
MiniMax Agent Chat Example
Use your MiniMax account (phone + password) to chat directly,
no need to manually copy JWT tokens!
"""

import asyncio
import sys
from typing import Optional

from auto_login import login_with_credentials
from minimax_adapter import web_agent_chat, parse_token


async def chat_once(username: str, password: str, message: str, model: str = "MiniMax-M2.7"):
    """
    Send a single chat message and get a response.
    
    Args:
        username: Phone number (e.g., "13800138000")
        password: Your MiniMax password
        message: The message to send
        model: Model name (default: MiniMax-M2.7)
    
    Returns:
        The AI response text
    """
    print(f"[*] Logging in with {username}...")
    jwt_token, real_user_id = await login_with_credentials(username, password)
    print(f"[+] Login successful! User ID: {real_user_id}")
    
    print(f"[*] Sending message...")
    messages = [{"role": "user", "content": message}]
    response = await web_agent_chat(model, messages, jwt_token, real_user_id)
    
    ai_response = response["choices"][0]["message"]["content"]
    print(f"\n🤖 AI Response:")
    print(ai_response)
    
    return ai_response


async def interactive_chat(username: str, password: str, model: str = "MiniMax-M2.7"):
    """
    Interactive chat session - chat back and forth!
    
    Args:
        username: Phone number
        password: Your password
        model: Model name
    """
    print(f"[*] Logging in with {username}...")
    jwt_token, real_user_id = await login_with_credentials(username, password)
    print(f"[+] Login successful! User ID: {real_user_id}")
    print(f"\n💬 Interactive Chat (type 'quit' or 'exit' to stop)\n")
    
    conversation_history = []
    
    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["quit", "exit", "q"]:
                print("👋 Goodbye!")
                break
            
            conversation_history.append({"role": "user", "content": user_input})
            
            print("🤖 AI: ", end="", flush=True)
            
            # Get response
            response = await web_agent_chat(model, conversation_history, jwt_token, real_user_id)
            ai_response = response["choices"][0]["message"]["content"]
            
            print(ai_response)
            print()
            
            conversation_history.append({"role": "assistant", "content": ai_response})
            
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")


def main():
    """Main function - parse arguments and run chat."""
    import argparse
    
    parser = argparse.ArgumentParser(description="MiniMax Agent Chat Example")
    parser.add_argument("--username", "-u", required=True, help="Phone number (e.g., 13800138000)")
    parser.add_argument("--password", "-p", required=True, help="MiniMax account password")
    parser.add_argument("--message", "-m", help="Single message mode (omit for interactive)")
    parser.add_argument("--model", default="MiniMax-M2.7", help="Model name (default: MiniMax-M2.7)")
    
    args = parser.parse_args()
    
    try:
        if args.message:
            asyncio.run(chat_once(args.username, args.password, args.message, args.model))
        else:
            asyncio.run(interactive_chat(args.username, args.password, args.model))
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
