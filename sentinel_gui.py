
import tkinter as tk
import Sentinel_Legacy

def main():
    root = tk.Tk()
    root.title("Sentinel GUI")
    root.geometry("300x200")
    label = tk.Label(root, text="Sentinel GUI loaded", font=("Arial", 14))
    label.pack(pady=50)
    root.mainloop()

if __name__ == "__main__":
    main()
