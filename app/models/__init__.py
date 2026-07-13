"""Models SQLAlchemy. Todo dado é escopado por user_id; deleções usam cascade."""
from app.models.user import User
from app.models.message import Message
from app.models.conversation_summary import ConversationSummary
from app.models.ai_note import AiNote
from app.models.user_profile import UserProfile
from app.models.reminder import Reminder
from app.models.job import Job
from app.models.notification_queue import NotificationQueue
from app.models.shell_command import ShellCommand
from app.models.shell_approval import ShellApproval
from app.models.saved_command import SavedCommand
from app.models.routine import Routine

__all__ = [
    "User",
    "Message",
    "ConversationSummary",
    "AiNote",
    "UserProfile",
    "Reminder",
    "Job",
    "NotificationQueue",
    "ShellCommand",
    "ShellApproval",
    "SavedCommand",
    "Routine",
]
