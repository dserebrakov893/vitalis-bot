from bot.db.models import User
from bot.services.registration import Profile


def profile(name: str = "Иванов Иван Иванович", newsletter: bool = False) -> Profile:
    return Profile(
        full_name=name,
        phone="+79990000000",
        email="ivan@example.com",
        university="Сеченовский",
        status="student",
        interests=["medtech"],
        project_stage="idea",
        age_group="adult",
        newsletter_opt_in=newsletter,
    )


async def add_users(sm, ids):
    async with sm() as s, s.begin():
        for i in ids:
            s.add(User(id=i, interests=[]))
