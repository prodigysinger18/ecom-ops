from app.core.db import engine
from app.models import RolloutControl, ConversationOutcome

if __name__=="__main__":
    for model in (RolloutControl,ConversationOutcome):model.__table__.create(engine,checkfirst=True)
    print("stage6 tables ready")
