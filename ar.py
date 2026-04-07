import asyncio
from playwright.async_api import async_playwright
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)

# States
ASK_AADHAAR, ASK_CAPTCHA, ASK_OTP = range(3)

UIDAI_URL = "https://myaadhaar.uidai.gov.in/genricDownloadAadhaar"
BOT_TOKEN = "8690598957:AAH9R2UYALwuWL1v5Apf1x5qGxtqs8lUlJQ"

# Store browser sessions per user
sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Send /aadhaar to download your Aadhaar card."
    )

async def aadhaar_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔢 Please enter your *12-digit Aadhaar number:*",
                                     parse_mode="Markdown")
    return ASK_AADHAAR

async def handle_aadhaar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    aadhaar_no = update.message.text.strip()

    if not aadhaar_no.isdigit() or len(aadhaar_no) != 12:
        await update.message.reply_text("❌ Invalid number. Enter a valid 12-digit Aadhaar number.")
        return ASK_AADHAAR

    user_id = update.effective_user.id
    await update.message.reply_text("⏳ Loading UIDAI page, please wait...")

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    page = await browser.new_page()

    await page.goto(UIDAI_URL, wait_until="networkidle")
    await page.fill('input[placeholder="Enter Aadhaar Number"]', aadhaar_no)

    # Grab captcha image
    captcha_element = await page.query_selector('img[alt="captcha"]')
    captcha_path = f"/tmp/captcha_{user_id}.png"
    await captcha_element.screenshot(path=captcha_path)

    # Save session
    sessions[user_id] = {
        "playwright": playwright,
        "browser": browser,
        "page": page,
        "aadhaar": aadhaar_no
    }

    await update.message.reply_photo(
        photo=open(captcha_path, "rb"),
        caption="🔡 Enter the captcha text shown above:"
    )
    return ASK_CAPTCHA

async def handle_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    captcha_text = update.message.text.strip()
    session = sessions.get(user_id)

    if not session:
        await update.message.reply_text("❌ Session expired. Send /aadhaar again.")
        return ConversationHandler.END

    page = session["page"]

    # Fill captcha and request OTP
    await page.fill('input[placeholder="Enter Captcha"]', captcha_text)
    await page.click('button:has-text("Send OTP")')
    await page.wait_for_timeout(2000)

    # Check for error
    error = await page.query_selector('.error-message')
    if error:
        err_text = await error.inner_text()
        await update.message.reply_text(f"❌ Error: {err_text}\nSend /aadhaar to try again.")
        await cleanup(user_id)
        return ConversationHandler.END

    await update.message.reply_text("✅ OTP sent to your registered mobile!\n📲 Enter the OTP:")
    return ASK_OTP

async def handle_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    otp = update.message.text.strip()
    session = sessions.get(user_id)

    if not session:
        await update.message.reply_text("❌ Session expired. Send /aadhaar again.")
        return ConversationHandler.END

    page = session["page"]

    await page.fill('input[placeholder="Enter OTP"]', otp)
    await page.click('button:has-text("Download Aadhaar")')
    await page.wait_for_timeout(3000)

    # Wait for download
    async with page.expect_download(timeout=15000) as download_info:
        download = await download_info.value

    pdf_path = f"/tmp/aadhaar_{user_id}.pdf"
    await download.save_as(pdf_path)

    await update.message.reply_document(
        document=open(pdf_path, "rb"),
        filename="Aadhaar.pdf",
        caption="✅ Here's your Aadhaar PDF!\n🔐 Password: first 4 letters of name (CAPS) + birth year\nExample: `RAHUL2001` → `RAHU2001`",
        parse_mode="Markdown"
    )

    await cleanup(user_id)
    return ConversationHandler.END

async def cleanup(user_id):
    session = sessions.pop(user_id, None)
    if session:
        await session["browser"].close()
        await session["playwright"].stop()

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await cleanup(user_id)
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("aadhaar", aadhaar_start)],
        states={
            ASK_AADHAAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_aadhaar)],
            ASK_CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_captcha)],
            ASK_OTP:     [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_otp)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    print("🤖 Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()