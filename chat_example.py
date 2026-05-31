"""
MiniMax Agent Chat Example
Use your MiniMax account (phone + password) to chat directly!
"""

import asyncio
import sys
from typing import Optional

from auto_login import login_with_credentials
from minimax_adapter import web_agent_chat


async def chat_once(
    username: str, 
    password: str, 
    message: str, 
    model: str = "MiniMax-M2.7",
    jwt_token: Optional[str] = None,
    user_id: Optional[str] = None
):
    """
    Send a single chat message and get a response.
    
    Args:
        username: Phone number (e.g., "13800138000")
        password: Your MiniMax password
        message: The message to send
        model: Model name (default: MiniMax-M2.7)
        jwt_token: Optional - if you already have a token, skip login
        user_id: Optional - required if jwt_token is provided
    
    Returns:
        The AI response text
    """
    if jwt_token and user_id:
        print(f"[*] Using provided JWT token...")
    else:
        print(f"[*] Logging in with {username}...")
        jwt_token, user_id = await login_with_credentials(username, password)
        print(f"[+] Login successful! User ID: {user_id}")
    
    print(f"[*] Sending message...")
    messages = [{"role": "user", "content": message}]
    response = await web_agent_chat(model, messages, jwt_token, user_id)
    
    ai_response = response["choices"][0]["message"]["content"]
    print(f"\n🤖 AI Response:")
    print(ai_response)
    print()
    
    return ai_response


async def interactive_chat(
    username: str, 
    password: str, 
    model: str = "MiniMax-M2.7",
    jwt_token: Optional[str] = None,
    user_id: Optional[str] = None
):
    """
    Interactive chat session - chat back and forth!
    
    Args:
        username: Phone number
        password: Your password
        model: Model name
        jwt_token: Optional - if you already have a token, skip login
        user_id: Optional - required if jwt_token is provided
    """
    if jwt_token and user_id:
        print(f"[*] Using provided JWT token...")
    else:
        print(f"[*] Logging in with {username}...")
        jwt_token, user_id = await login_with_credentials(username, password)
        print(f"[+] Login successful! User ID: {user_id}")
    
    print(f"\n💬 Interactive Chat (type 'quit' or 'exit' to stop)")
    print("="*60)
    
    conversation_history = []
    
    while True:
        try:
            user_input = input("\n👤 You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["quit", "exit", "q"]:
                print("👋 Goodbye!")
                break
            
            conversation_history.append({"role": "user", "content": user_input})
            
            print("🤖 AI: ", end="", flush=True)
            
            # Get response
            response = await web_agent_chat(model, conversation_history, jwt_token, user_id)
            ai_response = response["choices"][0]["message"]["content"]
            
            print(ai_response)
            
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
    parser.add_argument("--username", "-u", help="Phone number (e.g., 13800138000)")
    parser.add_argument("--password", "-p", help="MiniMax account password")
    parser.add_argument("--message", "-m", help="Single message mode (omit for interactive)")
    parser.add_argument("--model", default="MiniMax-M2.7", help="Model name (default: MiniMax-M2.7)")
    parser.add_argument("--token", "-t", help="Optional: Use existing JWT token instead of logging in")
    parser.add_argument("--user-id", help="Optional: User ID (required with --token)")
    
    args = parser.parse_args()
    
    # Check if we have what we need
    has_creds = args.username and args.password
    has_token = args.token and args.user_id
    
    if not has_creds and not has_token:
        print("Error: You must provide either --username/--password or --token/--user-id")
        parser.print_help()
        sys.exit(1)
    
    try:
        if args.message:
            asyncio.run(chat_once(
                args.username, 
                args.password, 
                args.message, 
                args.model,
                args.token,
                args.user_id
            ))
        else:
            asyncio.run(interactive_chat(
                args.username, 
                args.password, 
                args.model,
                args.token,
                args.user_id
            ))
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
