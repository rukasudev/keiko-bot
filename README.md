## 🥏 Corgi Bot
![e0bb502e8654548d89c00ec35571bba8](https://github.com/rukasudev/corgi-bot/assets/47928835/1bbda29f-f763-4f43-abec-09ada58012c7)
---
Corgi Bot is a powerful and versatile Discord bot built using Python, designed to streamline server moderation and enhance user experience. It incorporates various technologies to provide essential functionalities for managing and engaging your Discord server.

### 🛠️ Technologies Used

Corgi Bot leverages the following technologies:

- 🐍 Python
- 🍃 MongoDB
- 🧸 Redis
- 🐳 Docker
- 📦 Docker Compose
- 🌍 i18n (Internationalization)

### 🚀 Get Started

To get started with Corgi Bot, follow these steps:

1. 📥 Clone the repository.
2. 🔧 Configure your Discord bot token and other settings in the `.env` file.
3. ▶️ Run the bot using one of the following methods:

   - ⌨️ **Via Terminal**: Execute the following commands in the project's root directory:
   
     Install the required dependencies specified in the `requirements.txt` file:
     ```shell
     pip install -r requirements.txt
     ```

     Run the project in the terminal:
     ```shell
     python __main__.py
     ```

   - 🛠️ **Via Makefile**: Ensure you have `make` installed on your system. If not, run the following command to install it:
     ```shell
     sudo apt-get install make
     ```

     Once `make` is installed, you can set up the project using the following command:
     ```shell
     make setup
     ```

     And then, run the project using the following command:
     ```shell
     make run
     ```

   - 🐳 **Via Docker/Docker Compose**: Make sure you have Docker and Docker Compose installed on your machine. Use the provided `Dockerfile` and `docker-compose.yml` files to build and run the project:
     ```shell
     # Build the Docker image
     docker build -t corgi-bot .

     # Run the bot using Docker
     docker run corgi-bot

     # Or, using Docker Compose
     docker-compose up
     ```

4. 🎉 Enjoy the enhanced moderation and engagement features offered by Corgi Bot in your Discord server!



### 💬 Contributions

Contributions to Corgi Bot are welcome! If you have any ideas for new features or improvements, please feel free to submit a pull request.

### 📝 License

Corgi Bot is licensed under the [MIT License](https://opensource.org/licenses/MIT). Please refer to the `LICENSE` file for more information.
