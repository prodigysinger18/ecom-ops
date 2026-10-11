from app.core.db import engine
from app.models import ControlledAction, ReturnDraft, NotificationOutbox


if __name__ == "__main__":
    for model in (ControlledAction, ReturnDraft, NotificationOutbox):
        model.__table__.create(engine,checkfirst=True)
    print("stage5 tables ready")
