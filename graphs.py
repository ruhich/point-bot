import matplotlib.pyplot as plt
from datetime import datetime
import io

async def generate_activity_graph(data, chat_id, year, month):
    if not data:
        return None

    days = [datetime.strptime(row[0], '%Y-%m-%d').day for row in data]
    scores = [row[1] for row in data]

    plt.figure(figsize=(10, 6))
    plt.bar(days, scores, color='skyblue')
    plt.xlabel('День месяца')
    plt.ylabel('Сумма изменения кармы')
    plt.title(f'Активность кармы в чате {chat_id} за {month}/{year}')
    plt.xticks(days)
    plt.grid(axis='y', linestyle='--')
    plt.tight_layout()

    buffer = io.BytesIO()
    plt.savefig(buffer, format='png')
    buffer.seek(0)
    plt.close() 
    return buffer
