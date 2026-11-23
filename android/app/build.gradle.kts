plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val releaseStore = providers.environmentVariable("SLYNOR_SIGNING_STORE_FILE").orNull
val upgradeFixture = providers.gradleProperty("slynorUpgradeFixture").orNull == "true"

android {
    namespace = "io.github.hyoreku.slynor"
    compileSdk = 36
    buildToolsVersion = "36.0.0"

    defaultConfig {
        applicationId = "io.github.hyoreku.slynor"
        minSdk = 26
        targetSdk = 36
        versionCode = if (upgradeFixture) 8 else 9
        versionName = if (upgradeFixture) "0.8.0" else "0.9.0"
    }

    signingConfigs {
        if (releaseStore != null) {
            create("distribution") {
                storeFile = file(releaseStore)
                storePassword = providers.environmentVariable("SLYNOR_SIGNING_STORE_PASSWORD").get()
                keyAlias = providers.environmentVariable("SLYNOR_SIGNING_KEY_ALIAS").get()
                keyPassword = providers.environmentVariable("SLYNOR_SIGNING_KEY_PASSWORD").get()
            }
        }
    }
    buildFeatures { compose = true }
    buildTypes {
        debug { applicationIdSuffix = ".debug" }
        release { signingConfig = signingConfigs.findByName("distribution") }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin { compilerOptions.jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }

dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.10.01"))
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.media3:media3-exoplayer:1.9.2")
    implementation("androidx.media3:media3-session:1.9.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
}
