import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

from config import BOT_TOKEN, SUPER_ADMIN_ID, DB_NAME
from db import Database
from graphs import generate_activity_graph

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db = Database(DB_NAME)
scheduler = AsyncIOScheduler()

class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_admin_user_id_to_remove = State()


async def is_super_admin(user_id: int) -> bool:
    return user_id == SUPER_ADMIN_ID

async def check_group_admin(member_id: int, chat_id: int) -> bool:
    try:
        chat_member = await bot.get_chat_member(chat_id, member_id)
        return chat_member.status in ['creator', 'administrator']
    except Exception as e:
        logging.error(f"Error checking group admin for {member_id} in {chat_id}: {e}")
        return False
    

@dp.chat_member()
async def on_user_left(event: types.ChatMemberUpdated):
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    user_id = event.from_user.id
    chat_id = event.chat.id

    if old_status in ["member", "restricted", "administrator"] and new_status in ["left", "kicked"]:
        db.cursor.execute('DELETE FROM users WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
        db.conn.commit()

        logging.info(f"Пользователь {user_id} вышел из чата {chat_id}. Статистика удалена/обнулена.")


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.chat.type == 'private':
        await message.answer("Привет! Я бот для управления баллами в чатах. Добавь меня в группу и назначь админов для работы.")
        if await is_super_admin(message.from_user.id):
            await message.answer(
                "Ты Главный Администратор! Вот твоя панель управления:\n"
                "/admin_panel - Вывести панель управления главного админа."
            )
    else:
        await message.answer("Привет! Чтобы я начал работать, администратор чата должен назначить мне админов через личное сообщение со мной.")

@dp.message(Command("my_debug_id"))
async def cmd_my_debug_id(message: types.Message):
    await message.answer(f"Твой ID: {message.from_user.id}\nSUPER_ADMIN_ID из конфига: {SUPER_ADMIN_ID}")


@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    if message.chat.type == 'private':
        await message.answer("Эту команду нужно использовать в групповом чате.")
        return

    top_users = await db.get_top_users(message.chat.id)
    if not top_users:
        await message.answer("Топ еще пуст.")
        return

    response = f"{('Топ пользователей по баллам в этом месяце:')}\n"
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
    await message.answer(f"Твои баллы в этом чате за текущий месяц: {(score)}.")

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
        [InlineKeyboardButton(text="Показать админов чата", callback_data="super_admin_list_chats")],  # ИЗМЕНЕНО
        [InlineKeyboardButton(text="Показать график активности", callback_data="super_admin_activity_chats")], # ИЗМЕНЕНО
        [InlineKeyboardButton(text="Сбросить баллы вручную", callback_data="super_admin_reset_karma_chats")] # ИЗМЕНЕНО
    ])
    await message.answer("Панель управления главного админа:", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data.startswith('super_admin_'))
async def process_admin_callbacks(callback_query: types.CallbackQuery, state: FSMContext):
    if not await is_super_admin(callback_query.from_user.id):
        await callback_query.answer("Доступ запрещен.", show_alert=True)
        return

    action = callback_query.data
    await callback_query.answer() 

    chats = db.get_chats()
    if not chats:
        await callback_query.message.edit_text("Пока нет зарегистрированных чатов. Убедитесь, что бот получил хотя бы одно сообщение из каждого группового чата/канала, которым вы хотите управлять.")
        return 


    if action == "super_admin_add_admin":
        await callback_query.message.edit_text("Для добавления админа, укажите ID пользователя, которого хотите сделать админом. (После выбора чата)")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=chat_name, callback_data=f"select_chat_add_admin:{chat_id}")]
            for chat_id, chat_name in chats
        ])
        await callback_query.message.answer("Выберите чат:", reply_markup=keyboard)

    elif action == "super_admin_remove_admin":
        await callback_query.message.edit_text("Для удаления админа, укажите ID пользователя, которого хотите удалить из админов. (После выбора чата)")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=chat_name, callback_data=f"select_chat_remove_admin:{chat_id}")]
            for chat_id, chat_name in chats
        ])
        await callback_query.message.edit_text("Выберите чат:", reply_markup=keyboard)


    elif action == "super_admin_list_chats":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=chat_name, callback_data=f"select_chat_list_admins:{chat_id}")]
            for chat_id, chat_name in chats
        ])
        await callback_query.message.edit_text("Выберите чат для просмотра админов:", reply_markup=keyboard)

    elif action == "super_admin_activity_chats":
         keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=chat_name, callback_data=f"select_chat_activity_graph:{chat_id}")]
            for chat_id, chat_name in chats
         ])
         await callback_query.message.edit_text("Выберите чат для просмотра графика активности:", reply_markup=keyboard)

    elif action == "super_admin_reset_karma_chats":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=chat_name, callback_data=f"select_chat_reset_karma:{chat_id}")]
            for chat_id, chat_name in chats
        ])
        await callback_query.message.edit_text("Выберите чат для сброса баллов:", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data.startswith('select_chat_'))
async def process_chat_selection(callback_query: types.CallbackQuery, state: FSMContext):
    if not await is_super_admin(callback_query.from_user.id):
        await callback_query.answer("Доступ запрещен.", show_alert=True)
        return

    action, chat_id = callback_query.data.split(":")
    chat_id = int(chat_id)
    await callback_query.answer()

    if action.startswith("select_chat_add_admin"):

        await callback_query.message.edit_text(f"Выбран чат ID {chat_id}.  Теперь укажите ID пользователя, которого хотите сделать админом.")
        await state.update_data(selected_chat_id=chat_id)
        await state.set_state(AdminStates.waiting_for_user_id)

    elif action.startswith("select_chat_remove_admin"):
        await callback_query.message.edit_text(f"Выбран чат ID {chat_id}. Теперь укажите ID пользователя, которого хотите удалить из админов.")
        await state.update_data(selected_chat_id=chat_id)
        await state.set_state(AdminStates.waiting_for_admin_user_id_to_remove)

    elif action.startswith("select_chat_list_admins"):
        await callback_query.message.edit_text(f"Выбран чат ID {chat_id}.  Запрашиваю список админов...")
        admin_ids = await db.get_chat_admins(chat_id)
        if not admin_ids:
             await callback_query.message.edit_text(f"В чате {chat_id} нет назначенных ботом админов.")
        else:
            response = f"Админы чата {chat_id}:\n"
            for admin_id in admin_ids:
                try:
                    member = await bot.get_chat_member(chat_id, admin_id)
                    response += f"- {(member.user.full_name if member.user.full_name else 'Неизвестный')} (ID: {admin_id})\n"
                except Exception as e:
                    logging.error(f"Error getting chat member for {admin_id} in {chat_id}: {e}")
                    response += f"- Неизвестный пользователь (ID: {admin_id})\n"
            await callback_query.message.edit_text(response)

    elif action.startswith("select_chat_activity_graph"):
         await callback_query.message.edit_text(f"Выбран чат ID {chat_id}. Генерирую график активности...")

         current_year = datetime.now().year
         current_month = datetime.now().month

         activity_data = await db.get_monthly_activity(chat_id, current_year, current_month)
         if not activity_data:
             await callback_query.message.edit_text(f"Нет данных об активности баллов в чате {chat_id} за текущий месяц.")

         else:
             graph_buffer = await generate_activity_graph(activity_data, chat_id, current_year, current_month)
             if graph_buffer:
                await bot.send_photo(callback_query.message.chat.id, photo=BufferedInputFile(graph_buffer.getvalue(), filename="activity_graph.png"))

             else:
                await callback_query.message.edit_text("Не удалось сгенерировать график.")

    elif action.startswith("select_chat_reset_karma"):
         await callback_query.message.edit_text(f"Выбран чат ID {chat_id}. Сбрасываю баллы...")

         db.cursor.execute('UPDATE users SET score = 0 WHERE chat_id = ?', (chat_id,))
         db.conn.commit()
         await callback_query.message.edit_text(f"Баллы в чате {chat_id} успешно сброшены вручную!")

@dp.message(AdminStates.waiting_for_user_id)
async def process_add_admin_data(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id): return

    try:
       user_id_to_add = int(message.text.strip())
       data = await state.get_data()
       chat_id = data.get("selected_chat_id")

       if chat_id is None:
           await message.answer("Чат не выбран. Начните сначала с выбора чата.")
           await state.clear()
           return

       await db.add_admin(user_id_to_add, chat_id)
       await message.answer(f"Пользователь {user_id_to_add} добавлен в админы чата {chat_id}.")

    except ValueError:
        await message.answer("Неверный формат ID пользователя. Пожалуйста, введите только число.")
    finally:
       await state.clear()


@dp.message(AdminStates.waiting_for_admin_user_id_to_remove)
async def process_remove_admin_data(message: types.Message, state: FSMContext):
    if not await is_super_admin(message.from_user.id): return

    try:
        user_id_to_remove = int(message.text.strip())
        data = await state.get_data()
        chat_id = data.get("selected_chat_id")

        if chat_id is None:
            await message.answer("Чат не выбран. Начните сначала с выбора чата.")
            await state.clear()
            return
        await db.remove_admin(user_id_to_remove, chat_id)
        await message.answer(f"Пользователь {user_id_to_remove} удален из админов чата {chat_id}.")

    except ValueError:
        await message.answer("Неверный формат ID пользователя. Пожалуйста, введите только число.")
    finally:
        await state.clear()

@dp.message(lambda m: m.text not in ['+1', '-1'])
async def get_chat_id_from_forward(message: types.Message):
    logging.info(f"Received message in get_chat_id_from_forward from {message.from_user.id}. Chat type: {message.chat.type}")

   # if message.text in ['+1', '-1']:
    #    return


    if message.chat.type in ['group', 'supergroup', 'channel']:
        db.add_chat(message.chat.id, message.chat.title or "Без названия", message.chat.type)
        logging.info(f"Chat {message.chat.title} (ID: {message.chat.id}) added/updated in database by get_chat_id_from_forward.")


    if message.forward_from_chat:
        chat_id = message.forward_from_chat.id
        chat_title = message.forward_from_chat.title if message.forward_from_chat.title else "Без названия"
        chat_type = message.forward_from_chat.type
        
        response = (
            f"Сообщение переслано из чата/канала:\n"
            f"ID: {chat_id}\n"
            f"Название: {chat_title}\n"
            f"Тип: {chat_type}"
        )
        await message.answer(response)
        logging.info(f"Successfully identified forwarded chat ID: {chat_id}")
        return 
    if message.text == "/get_chat_id_debug":
        if message.chat.type in ['group', 'supergroup', 'channel']:
            await message.answer(f"ID этого чата: {message.chat.id}\nНазвание: {message.chat.title if message.chat.title else 'Без названия'}")
        elif message.chat.type == 'private':
            await message.answer(f"Твой личный ID (и ID этого приватного чата с ботом): {message.from_user.id}")
        return


@dp.message()
async def handle_karma(message: types.Message):
    logging.info(f"Received message in handle_karma: {message.text} from {message.from_user.full_name} in chat {message.chat.title if message.chat.title else message.chat.type} (ID: {message.chat.id})")

    logging.info(f"Received message: {message.text}")
    logging.info(f"Chat type: {message.chat.type}")
    logging.info(f"Is reply: {bool(message.reply_to_message)}")

    # logging.info(f"Is chat admin: {is_chat_admin_status}")
    # logging.info(f"Is bot admin: {is_bot_admin_status}")

    logging.info(f"Message text stripped: '{message.text.strip()}'")


    
    if message.chat.type not in ['group', 'supergroup'] or not message.reply_to_message:
        logging.info("Conditions not met: Not a group chat or not a reply.")
        return

    is_chat_admin_status = await check_group_admin(message.from_user.id, message.chat.id)
    is_bot_admin_status = await db.is_admin(message.from_user.id, message.chat.id)
    logging.info(f"Sender {message.from_user.full_name} (ID: {message.from_user.id}) - is_chat_admin: {is_chat_admin_status}, is_bot_admin: {is_bot_admin_status}")

    if not (is_chat_admin_status or is_bot_admin_status):
        logging.info("Sender is not an authorized admin.")
        return

    target_user = message.reply_to_message.from_user
    logging.info(f"Target user: {target_user.full_name} (ID: {target_user.id})")

    if target_user.is_bot:
        logging.info("Target is a bot, skipping.")
        return
    if target_user.id == message.from_user.id:
        logging.info("Target is sender, skipping (self-karma).")
        await message.reply("Нельзя ставить баллы самому себе.")
        return

    score_change = 0
    if message.text.strip() == "+1":
        score_change = 1
    elif message.text.strip() == "-1":
        score_change = -1
    
    logging.info(f"Score change: {score_change}")

    if score_change != 0:
        await db.update_user_score(target_user.id, message.chat.id, score_change)
        await db.log_activity(message.chat.id, message.from_user.id, target_user.id, score_change)
        current_score = await db.get_user_score(target_user.id, message.chat.id)
        await message.reply(f"Баллы пользователя {target_user.full_name} изменены. Текущие баллы: {current_score}")
    else:
        logging.info("Message text is not +1 or -1.")

async def monthly_karma_reset():
    logging.info("Checking for monthly scores reset...")
    await db.reset_monthly_karma_if_needed()
    logging.info("Monthly scores reset check finished.")

async def main():
    scheduler.add_job(monthly_karma_reset, 'cron', hour=0, minute=1)
    scheduler.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    if not BOT_TOKEN:
        logging.error("Environment variable BOT_TOKEN is not set!")
        exit(1)
    
    SUPER_ADMIN_ID_ENV = os.getenv("SUPER_ADMIN_ID")
    if not SUPER_ADMIN_ID_ENV:
        logging.error("Environment variable SUPER_ADMIN_ID is not set!")
        exit(1)
    try:
        SUPER_ADMIN_ID = int(SUPER_ADMIN_ID_ENV)
    except ValueError:
        logging.error(f"SUPER_ADMIN_ID '{SUPER_ADMIN_ID_ENV}' is not a valid integer!")
        exit(1)

    asyncio.run(main())