package com.example.util;

public final class Greeter {
    private Greeter() {
    }

    public static String greet(String who) {
        return "hello from a " + who;
    }
}
