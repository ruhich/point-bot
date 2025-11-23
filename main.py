from dotenv import load_dotenv
load_dotenv() 
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.markdown import hbold, hlink
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import logging

from config import BOT_TOKEN, SUPER_ADMIN_ID, DB_NAME
from db import Database
from graphs import generate_activity_graph

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db = Database(DB_NAME)
scheduler = AsyncIOScheduler()

async def is_super_admin(user_id: int) -> bool:
    return user_id == SUPER_ADMIN_ID

async def check_group_admin(member_id: int, chat_id: int) -> bool:
    chat_member = await bot.get_chat_member(chat_id, member_id)
    return chat_member.status in ['creator', 'administrator']


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.chat.type == 'private':
        await message.answer("Привет! Я бот для управления кармой в чатах. Добавь меня в группу и назначь админов для работы.")
        if await is_super_admin(message.from_user.id):
            await message.answer(
                "Ты Главный Администратор! Вот твоя панель управления:\n"
                "/admin_panel - Вывести панель управления главного админа."
            )
    else:
        await message.answer("Привет! Чтобы я начал работать, администратор чата должен назначить мне админов через личное сообщение со мной.")

@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    if message.chat.type == 'private':
        await message.answer("Эту команду нужно использовать в групповом чате.")
        return

    top_users = await db.get_top_users(message.chat.id)
    if not top_users:
        await message.answer("Топ еще пуст.")
        return

    response = f"{('Топ пользователей по карме в этом месяце:')}\n"
    for i, (user_id, score) in enumerate(top_users):
        try:
            member = await bot.get_chat_member(message.chat.id, user_id)
            user_name = member.user.full_name
        except Exception:
            user_name = f"Пользователь ID:{user_id}"
        response += f"{i+1}. {(user_name)}: {score}\n"
    await message.answer(response)

@dp.message(Command("mystats"))
async def cmd_mystats(message: types.Message):
    if message.chat.type == 'private':
        await message.answer("Эту команду нужно использовать в групповом чате.")
        return

    score = await db.get_user_score(message.from_user.id, message.chat.id)
    await message.answer(f"Твоя карма в этом чате за текущий месяц: {(score)}.")

@dp.message()
async def handle_karma(message: types.Message):
    if message.chat.type not in ['group', 'supergroup'] or not message.reply_to_message:
        return

    is_chat_admin = await check_group_admin(message.from_user.id, message.chat.id)
    is_bot_admin = await db.is_admin(message.from_user.id, message.chat.id)

    if not (is_chat_admin or is_bot_admin):
        return

    target_user = message.reply_to_message.from_user
    if target_user.is_bot:
        return
    if target_user.id == message.from_user.id:
        await message.reply("Нельзя ставить карму самому себе.")
        return

    score_change = 0
    if message.text.strip() == "+1":
        score_change = 1
    elif message.text.strip() == "-1":
        score_change = -1
    
    if score_change != 0:
        await db.update_user_score(target_user.id, message.chat.id, score_change)
        await db.log_activity(message.chat.id, message.from_user.id, target_user.id, score_change)
        current_score = await db.get_user_score(target_user.id, message.chat.id)
        await message.reply(f"Карма пользователя {target_user.full_name} изменена. Текущая карма: {current_score}")

@dp.message(Command("admin_panel"))
async def cmd_admin_panel(message: types.Message):
    logging.info(f"Attempted /admin_panel by user {message.from_user.id} in chat type: {message.chat.type}")

    if not await is_super_admin(message.from_user.id):
        logging.warning(f"User {message.from_user.id} is NOT super admin. Access denied.")
        return await message.reply("Доступ запрещен или команда должна быть в личной переписке с ботом.")
    
    if message.chat.type != 'private':
        logging.warning(f"User {message.from_user.id} tried /admin_panel in non-private chat ({message.chat.type}). Access denied.")
        return await message.reply("Доступ запрещен или команда должна быть в личной переписке с ботом.")

    logging.info(f"User {message.from_user.id} is super admin and in private chat. Displaying admin panel.")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Добавить админа", callback_data="super_admin_add_admin")],
        [InlineKeyboardButton(text="Удалить админа", callback_data="super_admin_remove_admin")],
        [InlineKeyboardButton(text="Показать админов чата", callback_data="super_admin_list_admins")],
        [InlineKeyboardButton(text="Показать график активности", callback_data="super_admin_activity_graph")],
        [InlineKeyboardButton(text="Сбросить карму вручную", callback_data="super_admin_reset_karma_manual")]
    ])
    await message.answer("Панель управления главного админа:", reply_markup=keyboard)

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

class AdminStates(StatesGroup):
    waiting_for_chat_id = State()
    waiting_for_user_id = State()
    waiting_for_admin_user_id_to_remove = State()
    waiting_for_chat_id_for_list = State()
    waiting_for_chat_id_for_graph = State()
    waiting_for_chat_id_for_reset = State()

@dp.callback_query(lambda c: c.data.startswith('super_admin_'))
async def process_admin_callbacks(callback_query: types.CallbackQuery, state: FSMContext):
    if not await is_super_admin(callback_query.from_user.id):
        await callback_query.answer("Доступ запрещен.", show_alert=True)
        return

    action = callback_query.data
    await callback_query.answer() 

    if action == "super_admin_add_admin":
        await callback_query.message.edit_text("Для добавления админа, укажите ID чата, затем ID пользователя, которого хотите сделать админом. Пример: `123456789 987654321`")
        await state.set_state(AdminStates.waiting_for_chat_id)
    elif action == "super_admin_remove_admin":
        await callback_query.message.edit_text("Для удаления админа, укажите ID чата, затем ID пользователя, которого хотите удалить из админов. Пример: `123456789 987654321`")
        await state.set_state(AdminStates.waiting_for_admin_user_id_to_remove)
    elif action == "super_admin_list_admins":
        await callback_query.message.edit_text("Укажите ID чата, чтобы просмотреть список назначенных админов.")
        await state.set_state(AdminStates.waiting_for_chat_id_for_list)
    elif action == "super_admin_activity_graph":
        await callback_query.message.edit_text("Укажите ID чата для генерации графика активности.")
        await state.set_state(AdminStates.waiting_for_chat_id_for_graph)
    elif action == "super_admin_reset_karma_manual":
        await callback_query.message.edit_text("Укажите ID чата для ручного сброса кармы. Это действие сбросит всю карму в указанном чате до 0!")
        await state.set_state(AdminStates.waiting_for_chat_id_for_reset)


@dp.message(AdminStates.waiting_for_chat_id)
async def process_add_admin_data(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id): return
    
    try:
        chat_id_str, user_id_str = message.text.split()
        chat_id = int(chat_id_str)
        user_id_to_add = int(user_id_str)

        await db.add_admin(user_id_to_add, chat_id)
        await message.answer(f"Пользователь {user_id_to_add} добавлен в админы чата {chat_id}.")
    except ValueError:
        await message.answer("Неверный формат. Пожалуйста, введите ID чата и ID пользователя через пробел.")
    finally:
        await state.clear()

@dp.message(AdminStates.waiting_for_admin_user_id_to_remove)
async def process_remove_admin_data(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id): return

    try:
        chat_id_str, user_id_str = message.text.split()
        chat_id = int(chat_id_str)
        user_id_to_remove = int(user_id_str)

        await db.remove_admin(user_id_to_remove, chat_id)
        await message.answer(f"Пользователь {user_id_to_remove} удален из админов чата {chat_id}.")
    except ValueError:
        await message.answer("Неверный формат. Пожалуйста, введите ID чата и ID пользователя через пробел.")
    finally:
        await state.clear()

@dp.message(AdminStates.waiting_for_chat_id_for_list)
async def process_list_admins_data(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id): return

    try:
        chat_id = int(message.text.strip())
        admin_ids = await db.get_chat_admins(chat_id)
        
        if not admin_ids:
            await message.answer(f"В чате {chat_id} нет назначенных ботом админов.")
            await state.clear()
            return

        response = f"Админы чата {chat_id}:\n"
        for admin_id in admin_ids:
            try:
                member = await bot.get_chat_member(chat_id, admin_id)
                response += f"- {hbold(member.user.full_name)} (ID: {admin_id})\n"
            except Exception:
                response += f"- Неизвестный пользователь (ID: {admin_id})\n"
        await message.answer(response)
    except ValueError:
        await message.answer("Неверный формат ID чата.")
    finally:
        await state.clear()

@dp.message(AdminStates.waiting_for_chat_id_for_graph)
async def process_activity_graph_data(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id): return
    
    try:
        chat_id = int(message.text.strip())
        current_year = datetime.now().year
        current_month = datetime.now().month

        activity_data = await db.get_monthly_activity(chat_id, current_year, current_month)
        if not activity_data:
            await message.answer(f"Нет данных об активности кармы в чате {chat_id} за текущий месяц.")
            return

        graph_buffer = await generate_activity_graph(activity_data, chat_id, current_year, current_month)
        if graph_buffer:
            await bot.send_photo(message.chat.id, photo=types.BufferedInputFile(graph_buffer.getvalue(), filename="activity_graph.png"))
        else:
            await message.answer("Не удалось сгенерировать график.")
    except ValueError:
        await message.answer("Неверный формат ID чата.")
    finally:
        await state.clear()

@dp.message(AdminStates.waiting_for_chat_id_for_reset)
async def process_reset_karma_data(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id): return
    
    try:
        chat_id = int(message.text.strip())
        db.cursor.execute('UPDATE users SET score = 0 WHERE chat_id = ?', (chat_id,))
        db.conn.commit()
        await message.answer(f"Карма в чате {chat_id} успешно сброшена вручную!")
    except ValueError:
        await message.answer("Неверный формат ID чата.")
    finally:
        await state.clear()

async def monthly_karma_reset():
    logging.info("Checking for monthly karma reset...")
    await db.reset_monthly_karma_if_needed()
    logging.info("Monthly karma reset check finished.")

async def main():
    scheduler.add_job(monthly_karma_reset, 'cron', hour=0, minute=1)
    scheduler.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN is not set!")
        exit(1)
    if not SUPER_ADMIN_ID:
        logging.error("SUPER_ADMIN_ID is not set!")
        exit(1)

    asyncio.run(main())
