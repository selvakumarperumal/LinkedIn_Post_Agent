from langgraph.graph import StateGraph, START, END
from langchain_anthropic.chat_models import ChatAnthropic
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field
from typing import Annotated, List, Dict, Any
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, BaseMessage
from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from langgraph.graph.message import add_messages

# Load environment variables from .env file
load_dotenv("./.env", override=True)

class ModelConfig(BaseSettings):
    """Configuration class for the Anthropic model settings"""
    anthropic_api_key: str
    model: str
    temperature: float

class State(BaseModel):
    """
    Application state that maintains conversation context.
    Uses Annotated fields with add_messages to automatically merge message lists.
    """
    linkedin_topic: str  # The topic for LinkedIn post generation
    # Generated posts are stored as a list of messages with automatic merging
    generated_post: Annotated[List[BaseMessage], Field(default_factory=list), add_messages]
    # Human feedback is stored as a list of messages with automatic merging
    human_feedback: Annotated[List[BaseMessage], Field(default_factory=list), add_messages]

class AnthropicChatModel(ChatAnthropic):
    """Custom wrapper for Anthropic ChatModel with LinkedIn post generation capability"""
    
    def __init__(self, config: ModelConfig):
        """Initialize the model with configuration settings"""
        super().__init__(
            api_key=config.anthropic_api_key,
            model_name=config.model,
            temperature=config.temperature,
        )

    def generate_linkedin_post(self, state: State) -> Any:
        """
        Generate a LinkedIn post using the LLM, incorporating human feedback.
        
        Args:
            state: Current application state containing topic and feedback
            
        Returns:
            Dictionary with updated generated_post and human_feedback
        """
        print("[model] Generating content")
        linkedin_topic = state.linkedin_topic
        # Get the latest feedback or use default message if none exists
        feedback = state.human_feedback if state.human_feedback else [HumanMessage(content="No Feedback yet")]

        error = ""
        max_retries = 3

        print("--------------------------------")
        print(state.generated_post)
        print(f"[model] Attempting to generate LinkedIn post for topic: {linkedin_topic}")
        print(f"[model] Current feedback count: {len(feedback)}")
        print("--------------------------------")
        # Retry mechanism for robust error handling
        for attempt in range(max_retries):
            try:
                # Construct the prompt with topic and latest feedback
                prompt = f"""
                    LinkedIn Topic: {linkedin_topic}
                    Human Feedback: {feedback[-1].content if feedback else "No feedback yet"}

                    Generate a structured and well-written LinkedIn post based on the given topic. In just 50 words,
                    make it engaging and professional.

                    Consider previous human feedback to refine the response.

                    {error}
                """

                # Invoke the LLM with system and human messages
                response = self.invoke([
                    SystemMessage(content="You are an expert LinkedIn content writer"), 
                    HumanMessage(content=prompt)
                ])
                
                generated_linkedin_post = str(response.content)
                print(f"[model_node] Generated post:\n{generated_linkedin_post}\n")
                
                # Return state updates - these will be automatically merged into existing state
                return {
                   "generated_post": [AIMessage(content=generated_linkedin_post)] , 
                   "human_feedback": feedback
                }
            except Exception as e:
                error = f"Error occurred: {str(e)}"
                print(f"Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise e

def human_node(state: State):
    """
    Human intervention node that pauses execution for user feedback.
    
    Args:
        state: Current application state
        
    Returns:
        Either Command(goto=END) to finish, or updated state with new feedback
    """
    print("\n[human_node] awaiting for human feedback...")
    print("\nGenerated LinkedIn Post:\n")

    # Interrupt execution and wait for human input
    # This pauses the graph until user provides feedback
    user_feedback = interrupt(
        {
            "generated_post": state.generated_post,
            "message": "Please provide your feedback on the generated LinkedIn post (or type 'done' to finish): ",
        }
    )       

    # Check if user wants to end the conversation
    if user_feedback.strip().lower() == "done":
        print("[human_node] Human feedback is 'done'. Ending the process.")
        return Command(goto=END)  # Terminate the workflow
    else:
        print("[human_node] Continuing the process for further refinement.")
        # Return new feedback as HumanMessage - will be merged with existing feedback
        return {"human_feedback": [HumanMessage(content=user_feedback)]}

# Initialize workflow components
workflow = StateGraph(State)  # Create workflow with State schema
model = AnthropicChatModel(ModelConfig())  # Initialize the AI model
memory_saver = MemorySaver()  # Enable conversation persistence

# Define the workflow graph structure
workflow.add_node("model_node", model.generate_linkedin_post)  # AI generation node
workflow.add_node("human_node", human_node)  # Human feedback node

# Define the flow between nodes
workflow.add_edge(START, "model_node")  # Start with AI generation
workflow.add_edge("model_node", "human_node")  # AI output goes to human review
workflow.add_edge("human_node", "model_node")  # Human feedback loops back to AI

# Compile the graph with checkpointing and interrupt capability
graph = workflow.compile(
    checkpointer=memory_saver,  # Enables state persistence
    interrupt_before=["human_node"]  # Pause before human input
)

# Configuration for conversation thread persistence
thread_config = {"configurable": {
    "thread_id": "linkedin_multi_conversation_thread",
}}

# Initialize the starting state
state = State(linkedin_topic="The future of AI in healthcare")

if __name__ == "__main__":
    # Main execution loop
    for chunk in graph.stream(state, config=thread_config):
        for node_id, value in chunk.items():
            # Handle interrupts (when human input is needed)
            if(node_id == "__interrupt__"):
                while True: 
                    # Get user feedback
                    user_feedback = input("Provide feedback (or type 'done' when finished): ")

                    # Resume graph execution with user feedback
                    graph.invoke(Command(resume=user_feedback), config=thread_config)

                    # Exit loop if user indicates completion
                    if user_feedback.lower() == "done":
                        break