package com.lhc.transport.driver.location

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.lhc.transport.driver.data.DriverApiService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicBoolean

class GateTrackingService : Service() {
    companion object {
        const val ACTION_START = "com.lhc.transport.driver.action.START_GATE_TRACKING"
        const val ACTION_STOP = "com.lhc.transport.driver.action.STOP_GATE_TRACKING"
        const val EXTRA_SERVER_URL = "server_url"
        const val EXTRA_TOKEN = "access_token"
        const val EXTRA_BOOKING_ID = "booking_id"
        private const val CHANNEL_ID = "ssd_gate_tracking"
        private const val NOTIFICATION_ID = 42
        private const val TAG = "GateTracking"
    }

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val sendingFix = AtomicBoolean(false)
    private val fusedLocation by lazy { LocationServices.getFusedLocationProviderClient(this) }
    private var locationCallback: LocationCallback? = null
    private var bookingId: Int = 0
    private var serverUrl: String = ""
    private var token: String = ""

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopTracking()
            stopSelf()
            return START_NOT_STICKY
        }
        if (intent?.action != ACTION_START) {
            stopSelf()
            return START_NOT_STICKY
        }

        serverUrl = intent.getStringExtra(EXTRA_SERVER_URL).orEmpty()
        token = intent.getStringExtra(EXTRA_TOKEN).orEmpty()
        bookingId = intent.getIntExtra(EXTRA_BOOKING_ID, 0)
        if (serverUrl.isBlank() || token.isBlank() || bookingId <= 0) {
            stopSelf()
            return START_NOT_STICKY
        }
        startAsForeground()
        beginLocationUpdates()
        return START_NOT_STICKY
    }

    private fun startAsForeground() {
        createNotificationChannel()
        val notification = notification("Monitoring the 20 m SSD gate")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun beginLocationUpdates() {
        if (ContextCompat.checkSelfPermission(
                this, Manifest.permission.ACCESS_FINE_LOCATION
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            updateNotification("Precise location permission is missing")
            stopSelf()
            return
        }
        val callback = object : LocationCallback() {
            override fun onLocationResult(result: LocationResult) {
                val location = result.lastLocation ?: return
                if (!sendingFix.compareAndSet(false, true)) return
                serviceScope.launch {
                    try {
                        val response = DriverApiService(serverUrl, token).sendGateFix(
                            bookingId,
                            location.latitude,
                            location.longitude,
                            location.accuracy.toDouble()
                        )
                        if (!response.isSuccessful) {
                            updateNotification("Server update failed (${response.code()}); retrying")
                        } else {
                            val gate = response.body()
                            when {
                                gate?.finished == true -> {
                                    updateNotification("SSD gate Time OUT/IN recorded")
                                    stopTracking()
                                    stopSelf()
                                }
                                gate?.event == "SSD_GATE_EXIT" ->
                                    updateNotification("Time OUT recorded at the SSD gate")
                                gate?.event == "SSD_GATE_ENTRY" ->
                                    updateNotification("Time IN recorded at the SSD gate")
                                gate?.ignored == true ->
                                    updateNotification(gate.message ?: "Waiting for accurate GPS")
                                else ->
                                    updateNotification("Monitoring trip #$bookingId at the SSD gate")
                            }
                        }
                    } catch (error: Exception) {
                        Log.w(TAG, "Gate location update failed", error)
                        updateNotification("Connection unavailable; retrying when GPS updates")
                    } finally {
                        sendingFix.set(false)
                    }
                }
            }
        }
        locationCallback = callback
        val request = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 10_000L)
            .setMinUpdateIntervalMillis(5_000L)
            .setMinUpdateDistanceMeters(8f)
            .build()
        try {
            fusedLocation.requestLocationUpdates(request, callback, mainLooper)
        } catch (error: SecurityException) {
            Log.e(TAG, "Location permission was revoked", error)
            updateNotification("Location permission was revoked")
            stopSelf()
        }
    }

    private fun stopTracking() {
        locationCallback?.let { fusedLocation.removeLocationUpdates(it) }
        locationCallback = null
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "SSD gate GPS tracking",
                NotificationManager.IMPORTANCE_LOW
            )
            getSystemService(NotificationManager::class.java)
                .createNotificationChannel(channel)
        }
    }

    private fun notification(message: String): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("LHC Driver — SSD Gate GPS")
            .setContentText(message)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()

    private fun updateNotification(message: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIFICATION_ID, notification(message))
    }

    override fun onDestroy() {
        stopTracking()
        serviceScope.cancel()
        super.onDestroy()
    }
}
