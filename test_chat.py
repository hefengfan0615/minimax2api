"""
Test script to verify chat functionality with a valid token.
"""

import asyncio
from minimax_adapter import web_agent_chat

# 从刚才的浏览器会话中获取的JWT token和用户ID
# 注意：这是示例，请替换为你自己的真实token
TEST_JWT_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3ODM2NjU4MzksInVzZXIiOnsiaWQiOiI1MTcxNDkyNTQxOTI1NjIxNzYiLCJuYW1lIjoi5o6i57Si6ICFMjE3NiIsImF2YXRhciI6Imh0dHBzOi8vY2RuLmhhaWx1b2FpLmNvbS9wcm9kLzIwMjUtMDMtMTItMjAvdXNlcl9hdmF0YXIvMTc0MTc4MTIwMDQ2NDM3NjUxNy0yMTExOTE4Nzk0ODY2Njg4MDFvdmVyc2l6ZS5wbmciLCJkZXZpY2VJRCI6IiIsImlzQW5vbnltb3VzIjpmYWxzZX19.Q7Kb77VZzwp6Puvc-a9dWOxlXmKB9v5Ve53qmUUloFk"
TEST_USER_ID = "517149254192562176"


async def test_chat():
    """Test chat with the captured token."""
    print("="*60)
    print("🤖 MiniMax Chat Test")
    print("="*60)
    print(f"\n[*] User ID: {TEST_USER_ID}")
    print(f"[*] Token length: {len(TEST_JWT_TOKEN)}")
    print(f"[*] Token preview: {TEST_JWT_TOKEN[:50]}...")
    print()
    
    # 测试发送一条消息
    test_message = "你好，请介绍一下你自己"
    print(f"[1/2] Sending test message: '{test_message}'")
    
    messages = [{"role": "user", "content": test_message}]
    
    try:
        response = await web_agent_chat(
            "MiniMax-M2.7", 
            messages, 
            TEST_JWT_TOKEN, 
            TEST_USER_ID
        )
        
        ai_response = response["choices"][0]["message"]["content"]
        
        print(f"\n[2/2] ✅ Response received!")
        print("\n" + "-"*60)
        print("🤖 AI:")
        print(ai_response)
        print("-"*60)
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def interactive_chat_with_token():
    """Interactive chat using the captured token."""
    print("="*60)
    print("🤖 MiniMax Interactive Chat")
    print("="*60)
    print(f"\nUser ID: {TEST_USER_ID}")
    print("\nType 'quit' or 'exit' to stop.\n")
    
    conversation_history = []
    
    while True:
        try:
            user_input = input("👤 You: ").strip()
            
            if not user_input:
                continue
            if user_input.lower() in ["quit", "exit", "q"]:
                print("👋 Goodbye!")
                break
            
            conversation_history.append({"role": "user", "content": user_input})
            
            print("🤖 AI: ", end="", flush=True)
            
            response = await web_agent_chat(
                "MiniMax-M2.7", 
                conversation_history, 
                TEST_JWT_TOKEN, 
                TEST_USER_ID
            )
            
            ai_response = response["choices"][0]["message"]["content"]
            print(ai_response)
            
            conversation_history.append({"role": "assistant", "content": ai_response})
            print()
            
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        asyncio.run(interactive_chat_with_token())
    else:
        success = asyncio.run(test_chat())
        sys.exit(0 if success else 1)
