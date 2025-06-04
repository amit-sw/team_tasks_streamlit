"""OpenAI service module for the Task Management System."""
import os
import json
import logging
import datetime
from typing import Dict, Optional, Any, List
import streamlit as st
from langchain_core.pydantic_v1 import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging
import traceback

from src.tasks.task_service import task_service
from src.database.firestore import firestore_client
from src.database.models import AIChat
from src.ai.prompt_repository import prompt_repository
from pydantic import BaseModel

class FirestoreEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        try:
            return str(obj)
        except:
            return None

logger = logging.getLogger(__name__)

class NewTask(BaseModel):
    title: str
    description: Optional[str] = None
    notes: Optional[str] = None
    due_date: Optional[str] = None

class ModifiedTask(BaseModel):
    id: str
    title: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[str] = None


class TaskChanges(BaseModel):
    new_tasks: List[NewTask]
    modified_tasks: List[ModifiedTask]


class OpenAIService:
    def __init__(self):
        self.api_key = os.environ.get('OPENAI_API_KEY')
        self.model = os.environ.get('OPENAI_MODEL', 'gpt-4.1-mini')
        if not self.api_key:
            logger.error("OpenAI API key not found in environment variables")
            raise ValueError("Missing OpenAI API key")
        self.db = firestore_client
        self.collection = 'AI_chats'
    
    def process_chat(self, user_id: str, input_text: str) -> Dict[str, Any]:
        try:
            chat_data = {
                'user_id': user_id,
                'inputText': input_text
            }
            task_list = self._list_tasks(user_id)
            system_prompt = self._get_system_prompt()
            chat_id = self.db.create(self.collection, chat_data)
            response = self._call_openai(user_id, system_prompt, input_text, task_list, chat_id)
            self.db.update(self.collection, chat_id, {'Response': response})
            logger.info(f"Chat processed for user {user_id}")
            return {
                'id': chat_id,
                'response': response
            }
        except Exception as e:
            logger.error(f"Error processing chat for user {user_id}: {str(e)}")
            raise
    
    def _list_tasks(self, user_id: str):
        try:
            active_tasks = task_service.get_active_tasks(user_id)
            completed_tasks = task_service.get_completed_tasks(user_id)
            deleted_tasks = task_service.get_deleted_tasks(user_id)
            logger.info(f"Listing tasks for user {user_id}: Active:{active_tasks}, Completed:{completed_tasks}, Deleted:{deleted_tasks}")
            active_tasks_dict = [task.to_dict() for task in active_tasks]
            completed_tasks_dict = [task.to_dict() for task in completed_tasks]
            deleted_tasks_dict = [task.to_dict() for task in deleted_tasks]
            return {
                "active": active_tasks_dict,
                "completed": completed_tasks_dict,
                "deleted": deleted_tasks_dict
            }
        except Exception as e:
            logger.error(f"Error listing tasks: {str(e)}")
            return {"error": str(e)}
    
    def _add_task(self, user_id: str, task_data_str: str):
        try:
            task_data = json.loads(task_data_str)
            if "title" not in task_data:
                return {"error": "Task title is required"}

            #task_id = task_service.create_task(user_id, task_data)
            task_dict = {
                "title": task_data.get("title"),
                "description": task_data.get("description", ""),
                "notes": task_data.get("notes", ""),
                "due_date": task_data.get("due_date"),
                "status":"active"
            }
            task_id = task_service.create_task(user_id, task_dict)

            return {"success": True, "task_id": task_id}
        except Exception as e:
            logger.error(f"Error adding task: {str(e)}")
            return {"error": str(e)}
    
    def _update_task(self, user_id: str, task_id: str, update_data_str: str):
        try:
            update_data = json.loads(update_data_str)
            task_service.update_task(user_id, task_id, update_data)
            return {"success": True}
        except Exception as e:
            logger.error(f"Error updating task: {str(e)}")
            return {"error": str(e)}
    
    def _get_system_prompt(self) -> str:
        DEFAULT_PROMPT = """You are an expert Task manager. Given the user input which includes user's current task-list, first understand the user's goal and figure out what changes need to be made to user's task list."""
        try:
            prompt = prompt_repository.get_active_prompt("AI_Tasks")
            if prompt:
                return prompt.text
            else:
                logger.warning("No active AI_Tasks prompt found, using fallback")
                return DEFAULT_PROMPT
        except Exception as e:
            logger.error(f"Error getting system prompt: {str(e)}")
            return DEFAULT_PROMPT

    def _first_call(self, system_prompt: str, user_input: str, task_list: Dict[str, Any]) -> str:
        try:
            chat = ChatOpenAI(
                api_key=self.api_key,
                model=self.model,
                temperature=0.7
            )
            clean_input = user_input.strip()
            active_tasks_str = json.dumps(task_list.get('active', []), indent=2, cls=FirestoreEncoder)
            completed_tasks_str = json.dumps(task_list.get('completed', []), indent=2, cls=FirestoreEncoder)
            full_prompt = f"""{system_prompt}\n\nCurrent active tasks:\n{active_tasks_str}
            Completed tasks:\n{completed_tasks_str}
            Based on the user's request, determine what changes need to be made to the task list.
            List each change separately.
            """
            messages = [
                SystemMessage(content=full_prompt),
                HumanMessage(content=clean_input)
            ]
            logger.info(f"\n\n\nCalling OpenAI with structured output schema PYDANTIC-H tool")
            response = chat.invoke(messages)
            return response.content
        except Exception as e:
            logger.error(f"FIRST CALL:Error calling OpenAI API: {str(e)}")
            raise

    def _second_call(self, content1: str) -> TaskChanges:
        logger.info(f"\n\n\nCalling second-call {content1}")
        logger.debug(f"Entering _second_call. Received content1:\n{content1}")
        system_prompt = "You are an AI assistant that processes a list of task descriptions and structures them into new and modified tasks. Strictly adhere to the provided Pydantic model for the output format. Ensure all required fields are present for each task. The input text is a list of proposed changes."
        try:
            chat = ChatOpenAI(
                api_key=self.api_key,
                model=self.model,
                temperature=0.2 # Lower temperature for more deterministic structuring
            )
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=content1)
            ]
            response = chat.with_structured_output(TaskChanges).invoke(messages)
            logger.debug(f"Successfully structured output in _second_call: {response}")
            return response
        except Exception as e:
            detailed_error_message = f"Error Type: {type(e).__name__}\n"
            detailed_error_message += f"Error Args: {e.args}\n"
            # Attempt to get more details if it's an OpenAI/HTTP error
            if hasattr(e, 'response') and e.response is not None and hasattr(e.response, 'status_code'):
                detailed_error_message += f"API Response Status: {e.response.status_code}\n"
                detailed_error_message += f"API Response Headers: {e.response.headers}\n"
                try:
                    detailed_error_message += f"API Response JSON: {e.response.json()}\n"
                except ValueError:
                    detailed_error_message += f"API Response Text: {e.response.text}\n"
            
            logger.error(f"SECOND CALL: Error calling OpenAI API. Details:\n{detailed_error_message}\nContent1 that caused error (first 500 chars):\n{content1[:500]}\nTraceback:\n{traceback.format_exc()}")
            raise

    def __third_call(self, user_id: str, resp: TaskChanges) -> str:
        """Create and update tasks based on OpenAI response."""
        logger.info(f"\n\n\nCalling third-call {resp}")
        try:
            # Create new tasks
            for new_task in resp.new_tasks:
                task_data = new_task.dict(exclude_none=True)
                logger.debug(f"Creating task for {user_id}: {task_data}")
                task_service.create_task(user_id, task_data)

            # Update existing tasks
            for mod_task in resp.modified_tasks:
                task_id = mod_task.id
                raw_data = mod_task.dict(exclude={'id'})
                update_data = {k: v for k, v in raw_data.items() if v is not None}
                logger.debug(
                    f"Updating task {task_id} for {user_id} with {update_data}"
                )
                task_service.update_task(user_id, task_id, update_data)

            return "Tasks updated successfully."
        except Exception as e:
            logger.error(f"THIRD CALL: Error updating tasks - {str(e)}")
            return f"Error updating tasks: {str(e)}"
        
    def _call_openai(self, user_id: str, system_prompt: str, user_input: str,
                     task_list: Dict[str, Any], chat_id: str) -> str:
        """Call OpenAI and collect user feedback before returning."""

        pending_key = f"pending_chat_{chat_id}"

        if pending_key in st.session_state:
            data = st.session_state[pending_key]
            resp = data["resp"]
            final_response = data["final_response"]
        else:
            content1 = self._first_call(system_prompt, user_input, task_list)
            resp = self._second_call(content1)
            final_response = self.__third_call(user_id, resp)
            st.session_state[pending_key] = {
                "resp": resp,
                "final_response": final_response,
            }

        if not self.__collect_feedback(chat_id, resp):
            st.stop()

        st.session_state.pop(pending_key, None)
        logger.info(f"\n\n\nOpenAI response: {final_response}")
        return final_response

    def __collect_feedback(self, chat_id: str, resp: TaskChanges) -> bool:
        """Display a form to capture user feedback.

        Returns True when feedback has been submitted or the dialog was
        cancelled. Returns False otherwise.
        """

        feedback_key = f"feedback_submitted_{chat_id}"

        if st.session_state.get(feedback_key):
            return True

        with st.form(f"feedback_form_{chat_id}"):
            st.subheader("New Tasks")
            for t in resp.new_tasks:
                st.json(t.dict(exclude_none=True))
            st.subheader("Modified Tasks")
            for t in resp.modified_tasks:
                st.json(t.dict(exclude_none=True))
            rating = st.radio(
                "Are these updates correct?",
                ("👍", "👎"),
                key=f"rating_{chat_id}",
                horizontal=True,
            )
            text = st.text_area(
                "Additional feedback",
                key=f"text_{chat_id}",
            )
            submit = st.form_submit_button("Submit")
            cancel = st.form_submit_button("Cancel")

        if submit:
            self.db.update(
                self.collection,
                chat_id,
                {"feedbackRating": rating, "feedbackText": text},
            )
            st.session_state[feedback_key] = True
            st.success("Feedback recorded")
            return True

        if cancel:
            st.session_state[feedback_key] = True
            return True

        return False
    
    def _call_openai_old(self, system_prompt: str, user_input: str, task_list: Dict[str, Any]) -> str:
        try:
            chat = ChatOpenAI(
                api_key=self.api_key,
                model=self.model,
                temperature=0.7
            )
            clean_input = user_input.strip()
            active_tasks_str = json.dumps(task_list.get('active', []), indent=2, cls=FirestoreEncoder)
            completed_tasks_str = json.dumps(task_list.get('completed', []), indent=2, cls=FirestoreEncoder)
            full_prompt = f"""{system_prompt}\n\nCurrent active tasks:\n{active_tasks_str}
            Completed tasks:\n{completed_tasks_str}
            Based on the user's request, determine what changes need to be made to the task list.
            """
            messages = [
                SystemMessage(content=full_prompt),
                HumanMessage(content=clean_input)
            ]
            logger.info(f"\n\n\nCalling OpenAI with structured output schema PYDANTIC-H tool")
            try:
                # Use LangChain's structured output parsing
                response = chat.with_structured_output(TaskChanges).invoke(messages)

                # Convert response (TaskChanges) to dict for easier handling
                new_tasks = response.new_tasks
                modified_tasks = response.modified_tasks
                summary = response.summary

                logger.info(f"Response completed")
                logger.info(f"Response: {response}")
                logger.info(f"Structured response: {response}")

                for new_task in new_tasks:
                    task_data_str = json.dumps(new_task, cls=FirestoreEncoder)
                    self._add_task(user_id, task_data_str)
                for modified_task in modified_tasks:
                    task_id = modified_task.pop('id')
                    update_data_str = json.dumps(modified_task, cls=FirestoreEncoder)
                    self._update_task(user_id, task_id, update_data_str)
                return summary
            except Exception as e:
                logger.error(f"Error with structured output: {str(e)}")
                #response = chat.invoke(messages)
                #return response.content
                return None
        except Exception as e:
            logger.error(f"Error calling OpenAI API: {str(e)}")
            return f"I encountered an error while processing your request: {str(e)}"

# Create an instance for use in the application
openai_service = OpenAIService()
