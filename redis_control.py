"""
Redis Control Script for BTC Bot Simulation Mode
Toggle between simulation, live trading, and stopped modes without restarting
"""
import redis
import sys
import os
from dotenv import load_dotenv

load_dotenv()


def get_redis_client():
    """Get Redis client."""
    try:
        client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            db=int(os.getenv('REDIS_DB', 2)),
            decode_responses=True,
            socket_connect_timeout=5
        )
        client.ping()
        return client
    except Exception as e:
        print(f"✗ Redis connection failed: {e}")
        print(f"  Make sure Redis is running: redis-server")
        return None


def get_current_mode(client):
    """Get current simulation mode."""
    try:
        mode = client.get('btc_trading:simulation_mode')
        if mode is None:
            return None
        if mode == '1': return "SIMULATION"
        if mode == '0': return "LIVE"
        if mode == '-1': return "STOPPED"
        return "UNKNOWN"
    except Exception as e:
        print(f"✗ Error reading mode: {e}")
        return None


def set_mode(client, mode_val: str):
    """Set trading mode (1=SIM, 0=LIVE, -1=STOPPED)."""
    try:
        client.set('btc_trading:simulation_mode', mode_val)
        mode_text = "SIMULATION" if mode_val == '1' else "LIVE TRADING" if mode_val == '0' else "STOPPED"
        print(f"✓ Mode set to: {mode_text}")
        return True
    except Exception as e:
        print(f"✗ Error setting mode: {e}")
        return False


def display_status(client):
    """Display current status."""
    mode = get_current_mode(client)
    
    print("\n" + "=" * 60)
    print("BTC BOT - CURRENT STATUS")
    print("=" * 60)
    
    if mode is None:
        print("Status: ⚪ Not set (using default from .env)")
    elif mode == "SIMULATION":
        print("Status: 🟡 SIMULATION MODE")
        print("  - No real trades will be placed")
        print("  - Safe for testing")
    elif mode == "LIVE":
        print("Status: 🔴 LIVE TRADING MODE")
        print("  - REAL MONEY AT RISK!")
        print("  - Real orders will be placed")
    elif mode == "STOPPED":
        print("Status: 🛑 STOPPED MODE")
        print("  - Bot is paused")
        print("  - No signals or orders will be generated")
    else:
        print(f"Status: ⚪ Unknown ({mode})")
    
    print("=" * 60 + "\n")


def main():
    """Main control interface."""
    print("\n" + "=" * 60)
    print("BTC BOT - MODE CONTROL")
    print("=" * 60)
    
    # Connect to Redis
    client = get_redis_client()
    if not client:
        return
    
    # Show current status
    display_status(client)
    
    # Parse command
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command in ['sim', 'simulation', 'on']:
            print("Switching to SIMULATION mode...")
            set_mode(client, '1')
            display_status(client)
            
        elif command in ['live', 'off']:
            print("\n⚠️  WARNING: Switching to LIVE TRADING mode!")
            confirm = input("Type 'yes' to confirm: ")
            if confirm.lower() == 'yes':
                set_mode(client, '0')
                display_status(client)
            else:
                print("Cancelled.")
                
        elif command in ['stop', 'pause']:
            print("Switching to STOPPED mode...")
            set_mode(client, '-1')
            display_status(client)
            
        elif command in ['status', 'check']:
            # Already displayed above
            pass
            
        else:
            print(f"Unknown command: {command}")
            print("\nUsage:")
            print("  python redis_control.py sim       - Enable simulation mode")
            print("  python redis_control.py live      - Enable live trading")
            print("  python redis_control.py stop      - Stop the bot")
            print("  python redis_control.py status    - Show current status")
    else:
        # Interactive mode
        print("Commands:")
        print("  1. Enable simulation mode")
        print("  2. Enable live trading (⚠️ DANGEROUS!)")
        print("  3. STOP the bot")
        print("  4. Check status")
        print("  5. Exit")
        
        while True:
            try:
                choice = input("\nEnter choice (1-5): ").strip()
                
                if choice == '1':
                    set_mode(client, '1')
                    display_status(client)
                    
                elif choice == '2':
                    print("\n⚠️  WARNING: This will enable LIVE TRADING!")
                    confirm = input("Type 'yes' to confirm: ")
                    if confirm.lower() == 'yes':
                        set_mode(client, '0')
                        display_status(client)
                    else:
                        print("Cancelled.")
                        
                elif choice == '3':
                    set_mode(client, '-1')
                    display_status(client)
                    
                elif choice == '4':
                    display_status(client)
                    
                elif choice == '5':
                    print("Goodbye!")
                    break
                    
                else:
                    print("Invalid choice!")
                    
            except KeyboardInterrupt:
                print("\nGoodbye!")
                break


if __name__ == "__main__":
    main()