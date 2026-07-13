<script setup>
import { token } from './api.js'
import AuthView from './components/AuthView.vue'
import MainView from './components/MainView.vue'
</script>

<template>
  <AuthView v-if="!token" />
  <MainView v-else />
</template>
