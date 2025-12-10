# goals_definitions.py

# A library of all available goals.
# logic_key: used later for the math engine to check if goal is met
PREDETERMINED_GOALS = [
    {
        "id": 101,
        "title": "Consistency Rookie",
        "description": "FC 5 maps in a row (Any Difficulty)",
        "logic_key": "fc_streak_5",
        "icon": "🛡️"
    },
    {
        "id": 102,
        "title": "5-Star Conqueror",
        "description": "Accumulate 10 FCs on maps > 5.0 stars",
        "logic_key": "accumulate_5star_10",
        "icon": "⭐"
    },
    {
        "id": 103,
        "title": "Accuracy Master",
        "description": "Submit a play with > 99% Accuracy (min. 4 stars)",
        "logic_key": "single_acc_99",
        "icon": "🎯"
    },
    {
        "id": 104,
        "title": "Speed Demon",
        "description": "Pass a DT map > 6.0 stars",
        "logic_key": "pass_dt_6star",
        "icon": "⚡"
    }
]

def get_goal_by_id(goal_id):
    for goal in PREDETERMINED_GOALS:
        if goal["id"] == int(goal_id):
            return goal
    return None